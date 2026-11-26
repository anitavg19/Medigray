"""Respaldos de Medigray.

Protege la información de dos riesgos distintos:
1. Errores (algo se borró o se cargó mal): un respaldo automático diario de la base
   de datos queda en el disco de Railway (carpeta respaldos), se guardan los últimos 14.
2. Pérdida del disco o de la cuenta: el respaldo COMPLETO (base de datos + archivos)
   se descarga desde la página y se guarda fuera de Railway (Drive o red de la empresa).

Restaurar crea antes un respaldo del estado actual, para poder deshacerlo.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from starlette.background import BackgroundTask

CR = timezone(timedelta(hours=-6))
NOMBRE_DB = "medigray.sqlite3"
PATRON = re.compile(r"^respaldo-\d{8}-\d{6}-(automatico|manual|antes-de-restaurar)\.zip$")
CONSERVAR = {"automatico": 14, "manual": 10, "antes-de-restaurar": 5}
DIAS_AVISO = 7  # días sin respaldo completo descargado antes de mostrar alerta
_candado = threading.Lock()


def ahora_cr():
    return datetime.now(CR)


def register(m):
    app = m.app
    carpeta = Path(m.DATA) / "respaldos"
    carpeta.mkdir(parents=True, exist_ok=True)

    # ---------- utilidades ----------
    def copia_db(destino):
        """Copia consistente de la base de datos aunque la página esté en uso."""
        src = sqlite3.connect(m.DB, timeout=30)
        dst = sqlite3.connect(destino)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()

    def resumen_db(ruta):
        c = sqlite3.connect(ruta)
        try:
            tablas = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
        finally:
            c.close()

    def sha256(ruta):
        h = hashlib.sha256()
        with open(ruta, "rb") as f:
            for b in iter(lambda: f.read(1024 * 1024), b""):
                h.update(b)
        return h.hexdigest()

    def crear(tipo, usuario="sistema"):
        """Respaldo de la base de datos dentro del disco de Railway."""
        with _candado:
            fecha = ahora_cr()
            nombre = f"respaldo-{fecha:%Y%m%d-%H%M%S}-{tipo}.zip"
            while (carpeta / nombre).exists():  # dos respaldos en el mismo segundo
                time.sleep(1)
                fecha = ahora_cr()
                nombre = f"respaldo-{fecha:%Y%m%d-%H%M%S}-{tipo}.zip"
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / NOMBRE_DB
                copia_db(db)
                manifiesto = {"fecha": fecha.isoformat(), "tipo": tipo, "usuario": usuario, "contenido": "base de datos",
                              "sha256_db": sha256(db), "registros": resumen_db(db)}
                parcial = carpeta / (nombre + ".tmp")
                with zipfile.ZipFile(parcial, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(db, NOMBRE_DB)
                    z.writestr("manifiesto.json", json.dumps(manifiesto, ensure_ascii=False, indent=2))
                os.replace(parcial, carpeta / nombre)
            # conservar solo los más recientes de cada tipo
            for t, n in CONSERVAR.items():
                viejos = sorted(carpeta.glob(f"respaldo-*-{t}.zip"))[:-n]
                for v in viejos:
                    v.unlink(missing_ok=True)
            return nombre

    def lista():
        out = []
        for p in sorted(carpeta.glob("respaldo-*.zip"), reverse=True):
            if not PATRON.match(p.name):
                continue
            f = datetime.strptime(p.name[9:24], "%Y%m%d-%H%M%S")
            out.append({"nombre": p.name, "fecha": f.strftime("%d/%m/%Y %H:%M"), "tipo": p.name[25:-4].replace("-", " "),
                        "tamano": f"{p.stat().st_size / 1024:,.0f} KB", "dia": f.date()})
        return out

    def ruta_segura(nombre):
        if not PATRON.match(nombre or ""):
            raise HTTPException(400, "Nombre de respaldo inválido")
        p = carpeta / nombre
        if not p.exists():
            raise HTTPException(404, "Respaldo no encontrado")
        return p

    # ---------- respaldo automático diario ----------
    def ciclo():
        time.sleep(60)
        while True:
            try:
                hoy = ahora_cr().date()
                if not any(x["dia"] == hoy and x["tipo"] == "automatico" for x in lista()):
                    crear("automatico")
            except Exception as e:  # nunca debe tumbar la página
                print("Respaldo automático falló:", e)
            time.sleep(3600)

    if os.getenv("MEDIGRAY_SIN_RESPALDO_AUTOMATICO") != "1":
        threading.Thread(target=ciclo, daemon=True, name="respaldo-diario").start()

    # ---------- pantalla ----------
    @app.get("/respaldos", response_class=HTMLResponse)
    def respaldos(req: Request):
        u = m.require(req, "Administrador")
        with m.conn() as c:
            ult = c.execute("""SELECT au.created_at, us.email FROM audit au LEFT JOIN users us ON us.id=au.actor_id
                               WHERE au.action='DESCARGAR_RESPALDO_COMPLETO' ORDER BY au.id DESC LIMIT 1""").fetchone()
        ultimo, dias = None, None
        if ult:
            d = datetime.fromisoformat(ult["created_at"])
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            ultimo = {"fecha": d.astimezone(CR).strftime("%d/%m/%Y %H:%M"), "email": ult["email"]}
            dias = (datetime.now(timezone.utc) - d).days
        total, usado, libre = shutil.disk_usage(m.DATA)
        return m.view(req, "respaldos.html", respaldos=lista(), ultimo=ultimo, dias=dias, DIAS_AVISO=DIAS_AVISO,
                      espacio={"usado": f"{usado / 1024**2:,.0f} MB", "total": f"{total / 1024**2:,.0f} MB",
                               "pct": round(usado * 100 / total) if total else 0})

    @app.post("/respaldos/crear")
    def respaldos_crear(req: Request, csrf_token: str = Form(...)):
        u = m.require(req, "Administrador")
        m.csrf(req, csrf_token)
        nombre = crear("manual", u["email"])
        with m.conn() as c:
            m.audit(c, u["id"], "CREAR_RESPALDO", "Respaldo", None, new={"archivo": nombre})
        return m.go("/respaldos")

    @app.get("/respaldos/descargar/{nombre}")
    def respaldos_descargar(req: Request, nombre: str):
        u = m.require(req, "Administrador")
        p = ruta_segura(nombre)
        with m.conn() as c:
            m.audit(c, u["id"], "DESCARGAR_RESPALDO", "Respaldo", None, new={"archivo": nombre})
        return FileResponse(p, media_type="application/zip", filename=nombre, headers={"Cache-Control": "no-store"})

    @app.get("/respaldos/completo")
    def respaldos_completo(req: Request):
        """Base de datos + todos los archivos subidos, para guardar fuera de Railway."""
        u = m.require(req, "Administrador")
        fecha = ahora_cr()
        nombre = f"medigray-respaldo-completo-{fecha:%Y%m%d-%H%M}.zip"
        tmpdir = tempfile.mkdtemp()  # disco temporal del servidor, no el volumen
        zpath = Path(tmpdir) / nombre
        db = Path(tmpdir) / NOMBRE_DB
        copia_db(db)
        archivos = [p for p in Path(m.FILES).rglob("*") if p.is_file() and not p.name.startswith("temp_")]
        manifiesto = {"fecha": fecha.isoformat(), "tipo": "completo", "usuario": u["email"], "sha256_db": sha256(db),
                      "registros": resumen_db(db), "archivos": len(archivos)}
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db, NOMBRE_DB)
            for p in archivos:
                z.write(p, "files/" + p.relative_to(m.FILES).as_posix())
            z.writestr("manifiesto.json", json.dumps(manifiesto, ensure_ascii=False, indent=2))
            z.writestr("LEAME.txt", "Respaldo completo de Medigray: base de datos (medigray.sqlite3) y archivos subidos "
                                    "(carpeta files). Para restaurarlo, use la pantalla Respaldos > Restaurar.\n")
        with m.conn() as c:
            m.audit(c, u["id"], "DESCARGAR_RESPALDO_COMPLETO", "Respaldo", None, new={"archivo": nombre, "archivos": len(archivos)})
        return FileResponse(zpath, media_type="application/zip", filename=nombre, headers={"Cache-Control": "no-store"},
                            background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True))

    # ---------- restaurar ----------
    def restaurar_desde_zip(zpath, u):
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(zpath) as z:
                nombres = z.namelist()
                if NOMBRE_DB not in nombres:
                    raise HTTPException(400, "El archivo no es un respaldo de Medigray (falta la base de datos)")
                z.extract(NOMBRE_DB, tmp)
                db = Path(tmp) / NOMBRE_DB
                try:
                    tablas = resumen_db(db)
                except sqlite3.DatabaseError:
                    raise HTTPException(400, "La base de datos del respaldo está dañada")
                if "users" not in tablas or "arts" not in tablas:
                    raise HTTPException(400, "El respaldo no corresponde a Medigray")
                if not sqlite3.connect(db).execute("SELECT COUNT(*) FROM users WHERE role='Administrador' AND active=1").fetchone()[0]:
                    raise HTTPException(400, "El respaldo no tiene ningún administrador activo; no se restaura")
                previo = crear("antes-de-restaurar", u["email"])
                # 1) archivos: se agregan los que falten, nunca se borra ninguno
                agregados = 0
                for n in nombres:
                    if not n.startswith("files/") or n.endswith("/"):
                        continue
                    rel = Path(n[len("files/"):])
                    if rel.is_absolute() or ".." in rel.parts:
                        continue
                    dest = Path(m.FILES) / rel
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(n) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
                        agregados += 1
            # 2) base de datos: se reemplaza por la del respaldo
            src = sqlite3.connect(db)
            dst = sqlite3.connect(m.DB, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close(); src.close()
        return previo, agregados, tablas

    def registrar_restauracion(u, origen, previo, agregados):
        with m.conn() as c:
            admin = c.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (u["email"],)).fetchone()
            m.audit(c, admin["id"] if admin else None, "RESTAURAR_RESPALDO", "Respaldo", None,
                    new={"origen": origen, "respaldo_previo": previo, "archivos_agregados": agregados, "por": u["email"]})

    @app.post("/respaldos/restaurar/{nombre}")
    def restaurar_lista(req: Request, nombre: str, csrf_token: str = Form(...), confirmar: str = Form("")):
        u = m.require(req, "Administrador")
        m.csrf(req, csrf_token)
        if confirmar.strip().upper() != "RESTAURAR":
            raise HTTPException(400, "Escriba RESTAURAR para confirmar")
        previo, agregados, _ = restaurar_desde_zip(ruta_segura(nombre), u)
        registrar_restauracion(u, nombre, previo, agregados)
        req.session.clear()
        return m.go("/login")

    @app.post("/respaldos/restaurar")
    async def restaurar_subido(req: Request, csrf_token: str = Form(...), confirmar: str = Form(""), archivo: UploadFile = File(...)):
        u = m.require(req, "Administrador")
        m.csrf(req, csrf_token)
        if confirmar.strip().upper() != "RESTAURAR":
            raise HTTPException(400, "Escriba RESTAURAR para confirmar")
        if not (archivo.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "Suba el archivo .zip del respaldo")
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "subido.zip"
            with open(zpath, "wb") as out:
                while True:
                    b = await archivo.read(1024 * 1024)
                    if not b:
                        break
                    out.write(b)
            if not zipfile.is_zipfile(zpath):
                raise HTTPException(400, "El archivo no es un .zip válido")
            previo, agregados, _ = restaurar_desde_zip(zpath, u)
        registrar_restauracion(u, archivo.filename, previo, agregados)
        req.session.clear()
        return m.go("/login")
