"""Formulario Control de Cambios de Arte (XXX-001-F01) con doble propósito.

1. Se llena una sola vez en la plataforma y sus datos actualizan Medigray:
   al cerrarlo se crea la nueva revisión del arte y su estado por país, y la
   versión anterior pasa a vigencia transitoria u obsoleta.
2. El mismo registro se imprime o se guarda como PDF con el formato oficial
   del formulario, como comprobante para el expediente.

Migraciones aditivas: solo crea tablas nuevas (prefijo cc_). No modifica las existentes.
"""
import hashlib
import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

ESTADOS = ("Borrador", "En revisión de Calidad", "Devuelto", "Aprobado", "Cerrado", "Rechazado")
EDITABLES = ("Borrador", "Devuelto")
ROLES_REG = ("Administrador", "Regulatorios")
ROLES_CAL = ("Administrador", "Gestión de Calidad")
MATERIALES = ("Caja", "Etiqueta", "Inserto", "Etiqueta de vial", "Termoformado / blíster", "Otro")
ORIGENES = ("Regulatorio / prevención", "Ficha técnica", "Comercial / CCSS", "País nuevo", "Fabricante", "Producción",
            "Corrección de error")
ELEMENTOS = ("Texto", "Código de barras", "Registro sanitario", "Lote y vencimiento", "Braille", "Troquel / dimensiones", "Otro")
EST_REG = {"aprobado": "Aprobado por autoridad", "interno": "Interno (no requiere autoridad)", "pendiente": "Pendiente"}
DISPOSICION = ("Agotar hasta la fecha de D", "Pueden coexistir en bodega", "Descartar", "N/A")
FILAS_AFECT = 3      # filas que muestra el formulario (se pueden agregar más)
FILAS_PAISES = 4     # igual que el formulario impreso
MAX_FILAS = 40
# Lista de chequeo de la prueba de impresión (XXX-001-F02, basada en F-P-GC-15.02 de Oridama)
CHECKLIST = ("Incluye todo lo solicitado por el RTCA 11.01.02:04",
             "Incluye los requisitos de cada país de destino",
             "Incluye la frase con el código SAP y la versión de la prueba de impresión",
             "La letra es legible, sin errores, y el texto coincide con el arte aprobado",
             "El código de barras fue comparado contra los números correctos",
             "Incluye los registros sanitarios correspondientes a cada país",
             "Las dimensiones corresponden al troquel del fabricante",
             "El lote y el vencimiento están en la ubicación aprobada")
RONDAS = {1: ("Primera revisión (Asuntos Regulatorios)", ("Conforme", "Devuelta")),
          2: ("Segunda revisión (Aseguramiento de Calidad)", ("Aprobada", "Devuelta"))}


def register(m):
    app = m.app
    with m.conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS cc_forms(
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id),
            anio INTEGER NOT NULL, consecutivo INTEGER NOT NULL, numero TEXT NOT NULL,
            art_id INTEGER NOT NULL REFERENCES arts(id),
            estado TEXT NOT NULL DEFAULT 'Borrador',
            datos TEXT NOT NULL DEFAULT '{}',
            version_id INTEGER REFERENCES versions(id),
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(company_id, anio, consecutivo));
        CREATE TABLE IF NOT EXISTS cc_afectaciones(
            id INTEGER PRIMARY KEY, form_id INTEGER NOT NULL REFERENCES cc_forms(id),
            n INTEGER NOT NULL, elemento TEXT, dice TEXT, debe_decir TEXT, justificacion TEXT);
        CREATE TABLE IF NOT EXISTS cc_paises(
            id INTEGER PRIMARY KEY, form_id INTEGER NOT NULL REFERENCES cc_forms(id),
            country_id INTEGER NOT NULL REFERENCES countries(id),
            requiere_autoridad TEXT, estado_regulatorio TEXT, tramite TEXT, fecha_tramite TEXT,
            version_aplica TEXT, fin_vigencia_anterior TEXT, UNIQUE(form_id, country_id));
        CREATE TABLE IF NOT EXISTS cc_firmas(
            id INTEGER PRIMARY KEY, form_id INTEGER NOT NULL REFERENCES cc_forms(id),
            etapa TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
            decision TEXT, observaciones TEXT, fecha TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cc_prueba(
            id INTEGER PRIMARY KEY, form_id INTEGER NOT NULL REFERENCES cc_forms(id),
            ronda INTEGER NOT NULL, fabricante TEXT, respuestas TEXT NOT NULL, resultado TEXT NOT NULL,
            observaciones TEXT, user_id INTEGER NOT NULL REFERENCES users(id), fecha TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS cc_forms_company_idx ON cc_forms(company_id, estado);
        CREATE INDEX IF NOT EXISTS cc_forms_art_idx ON cc_forms(art_id);
        ''')

    # ---------- utilidades ----------
    def empresa(req, u):
        permitidas = m.company_ids(u)
        cid = req.session.get("empresa_actual")
        if cid not in permitidas:
            cid = permitidas[0] if permitidas else None
        if cid is None:
            raise HTTPException(403, "Sin empresa asignada")
        return cid

    def cargar(c, u, form_id):
        f = c.execute("SELECT * FROM cc_forms WHERE id=?", (form_id,)).fetchone()
        if not f:
            raise HTTPException(404, "Formulario no encontrado")
        m.company_access(u, f["company_id"])
        return f

    def contexto(c, f=None, art_id=None):
        """Reúne todo lo que muestran el formulario, el detalle y la impresión."""
        art_id = f["art_id"] if f else art_id
        art = c.execute("""SELECT a.*, co.name company FROM arts a JOIN companies co ON co.id=a.company_id
                           WHERE a.id=?""", (art_id,)).fetchone()
        if not art:
            raise HTTPException(404, "Arte no encontrado")
        ultima = c.execute("SELECT revision FROM versions WHERE art_id=? ORDER BY id DESC LIMIT 1", (art_id,)).fetchone()
        vigentes = c.execute("""SELECT p.name pais, v.revision, cv.local_version, cv.status FROM country_versions cv
            JOIN versions v ON v.id=cv.version_id JOIN countries p ON p.id=cv.country_id
            WHERE v.art_id=? AND cv.status IN ('Vigente Oficial','Vigente Transitorio') ORDER BY p.name""", (art_id,)).fetchall()
        countries = c.execute("SELECT id,name FROM countries ORDER BY CASE name WHEN 'Costa Rica' THEN 0 ELSE 1 END, name").fetchall()
        datos, afect, paises, paises_lista, firmas, pruebas = {}, [], {}, [], [], {}
        if f:
            datos = json.loads(f["datos"] or "{}")
            afect = c.execute("SELECT * FROM cc_afectaciones WHERE form_id=? ORDER BY n", (f["id"],)).fetchall()
            paises_lista = [dict(r) for r in c.execute("""SELECT cp.*, co.name pais FROM cc_paises cp JOIN countries co
                                                           ON co.id=cp.country_id WHERE cp.form_id=? ORDER BY cp.id""", (f["id"],))]
            paises = {r["country_id"]: r for r in paises_lista}
            firmas = c.execute("""SELECT fi.*, us.email FROM cc_firmas fi JOIN users us ON us.id=fi.user_id
                                  WHERE fi.form_id=? ORDER BY fi.id""", (f["id"],)).fetchall()
            for r in c.execute("""SELECT pr.*, us.email FROM cc_prueba pr JOIN users us ON us.id=pr.user_id
                                  WHERE pr.form_id=? ORDER BY pr.id""", (f["id"],)):
                x = dict(r); x["resp"] = json.loads(r["respuestas"]); pruebas[r["ronda"]] = x  # última de cada ronda
        else:
            datos = {"material": [], "origen": [], "codigos_sap": art["sap_code"],
                     "version_actual": ultima["revision"] if ultima else "", "fecha_solicitud": date.today().isoformat()}
        if not f:  # el formulario nuevo trae Costa Rica en la primera fila, como el impreso
            cr = next((co for co in countries if co["name"] == "Costa Rica"), None)
            if cr:
                paises_lista = [{"country_id": cr["id"], "pais": cr["name"]}]
        elaboro = next((fi for fi in firmas if fi["etapa"].startswith("Elaboró")), None)
        return dict(art=art, ultima=ultima, vigentes=vigentes, countries=countries, d=datos, afect=afect,
                    paises=paises, paises_lista=paises_lista, elaboro=elaboro, FILAS_PAISES=FILAS_PAISES, firmas=firmas, pruebas=pruebas, f=f, MATERIALES=MATERIALES, ORIGENES=ORIGENES,
                    ELEMENTOS=ELEMENTOS, EST_REG=EST_REG, DISPOSICION=DISPOSICION, FILAS_AFECT=FILAS_AFECT,
                    CHECKLIST=CHECKLIST, RONDAS=RONDAS)

    def firma(c, form_id, etapa, u, decision="", obs=""):
        c.execute("INSERT INTO cc_firmas(form_id,etapa,user_id,decision,observaciones,fecha) VALUES(?,?,?,?,?,?)",
                  (form_id, etapa, u["id"], decision, obs[:1000], m.utc()))

    def guardar_datos(c, form_id, fd, countries):
        g = lambda k: (fd.get(k) or "").strip()[:2000]
        datos = {
            "fecha_solicitud": g("fecha_solicitud"),
            "codigos_sap": g("codigos_sap"), "material": [x for x in fd.getlist("material") if x in MATERIALES],
            "material_otro": g("material_otro"),
            "version_actual": g("version_actual"), "version_propuesta": g("version_propuesta"),
            "origen": [x for x in fd.getlist("origen") if x in ORIGENES], "descripcion": g("descripcion"),
            "disposicion": g("disposicion") if g("disposicion") in DISPOSICION else "", "nota_sap": g("nota_sap"),
            "fabricante_actualizado": g("fabricante_actualizado"), "fecha_fabricante": g("fecha_fabricante"),
        }
        c.execute("UPDATE cc_forms SET datos=?, updated_at=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), m.utc(), form_id))
        c.execute("DELETE FROM cc_afectaciones WHERE form_id=?", (form_id,))
        n = 0
        for i in range(1, MAX_FILAS + 1):
            row = [g(f"af_{k}_{i}") for k in ("elemento", "dice", "debe")] + [""]
            if any(row[1:]):
                n += 1
                c.execute("INSERT INTO cc_afectaciones(form_id,n,elemento,dice,debe_decir,justificacion) VALUES(?,?,?,?,?,?)",
                          (form_id, n, *row))
        c.execute("DELETE FROM cc_paises WHERE form_id=?", (form_id,))
        validos = {co["id"] for co in countries}
        vistos = set()
        for i in range(1, MAX_FILAS + 1):
            pais, est = g(f"p_pais_{i}"), g(f"p_est_{i}")
            if not pais:
                continue
            try:
                k = int(pais)
            except ValueError:
                raise HTTPException(400, "País inválido")
            if k not in validos:
                raise HTTPException(400, "País inválido")
            if k in vistos:
                raise HTTPException(400, "Un país aparece dos veces en la sección D")
            if est not in EST_REG:
                raise HTTPException(400, "Indique el estado de la versión propuesta en cada país de la sección D")
            vistos.add(k)
            req_aut = {"aprobado": "Sí", "pendiente": "Sí", "interno": "No"}[est]
            c.execute("""INSERT INTO cc_paises(form_id,country_id,requiere_autoridad,estado_regulatorio,tramite,fecha_tramite,
                         version_aplica,fin_vigencia_anterior) VALUES(?,?,?,?,?,?,?,?)""",
                      (form_id, k, req_aut, est, g(f"p_tram_{i}"), g(f"p_ftram_{i}"), "", g(f"p_fin_{i}")))
        return n

    # ---------- listado ----------
    @app.get("/control-cambios", response_class=HTMLResponse)
    def cc_lista(req: Request, estado: str = ""):
        u = m.require(req)
        cid = empresa(req, u)
        with m.conn() as c:
            rows = c.execute("""SELECT f.*, a.master_code, a.sap_code, a.product FROM cc_forms f JOIN arts a ON a.id=f.art_id
                WHERE f.company_id=? AND (?='' OR f.estado=?) ORDER BY f.id DESC LIMIT 500""", (cid, estado, estado)).fetchall()
            arts = c.execute("SELECT id, master_code, sap_code, product FROM arts WHERE company_id=? ORDER BY product COLLATE NOCASE",
                             (cid,)).fetchall()
        lista = []
        for r in rows:
            x = dict(r)
            x["vp"] = json.loads(r["datos"] or "{}").get("version_propuesta", "")
            lista.append(x)
        return m.view(req, "cc_lista.html", forms=lista, arts=arts, estado=estado, ESTADOS=ESTADOS,
                      puede_crear=u["role"] in ROLES_REG)

    # ---------- crear ----------
    @app.get("/control-cambios/nuevo", response_class=HTMLResponse)
    def cc_nuevo(req: Request, art_id: int):
        u = m.require(req, *ROLES_REG)
        m.art_access(u, art_id)
        with m.conn() as c:
            if not c.execute("SELECT 1 FROM versions WHERE art_id=?", (art_id,)).fetchone():
                raise HTTPException(400, "El control de cambios solo aplica sobre un arte con una versión aprobada. "
                                         "Primero registre el arte base (procedimiento, sección 7.1).")
            ctx = contexto(c, art_id=art_id)
        return m.view(req, "cc_form.html", **ctx)

    @app.post("/control-cambios/nuevo")
    async def cc_crear(req: Request):
        u = m.require(req, *ROLES_REG)
        fd = await req.form()
        m.csrf(req, fd.get("csrf_token"))
        try:
            art_id = int(fd.get("art_id", ""))
        except ValueError:
            raise HTTPException(400, "Arte inválido")
        cid = m.art_access(u, art_id)
        anio = date.today().year
        with m.conn() as c:
            seq = c.execute("SELECT COALESCE(MAX(consecutivo),0)+1 FROM cc_forms WHERE company_id=? AND anio=?", (cid, anio)).fetchone()[0]
            numero = f"CC-{anio}-{seq:04d}"
            cur = c.execute("""INSERT INTO cc_forms(company_id,anio,consecutivo,numero,art_id,estado,created_by,created_at,updated_at)
                               VALUES(?,?,?,?,?,'Borrador',?,?,?)""", (cid, anio, seq, numero, art_id, u["id"], m.utc(), m.utc()))
            fid = cur.lastrowid
            countries = c.execute("SELECT id FROM countries").fetchall()
            guardar_datos(c, fid, fd, countries)
            firma(c, fid, "Elaboró (Asuntos Regulatorios)", u, "Creado")
            m.audit(c, u["id"], "CREAR", "ControlCambiosArte", fid, new={"numero": numero, "art_id": art_id})
        return m.go(f"/control-cambios/{fid}")

    # ---------- ver / editar ----------
    @app.get("/control-cambios/{form_id}", response_class=HTMLResponse)
    def cc_detalle(req: Request, form_id: int):
        u = m.require(req)
        with m.conn() as c:
            f = cargar(c, u, form_id)
            ctx = contexto(c, f)
        return m.view(req, "cc_detalle.html", ROLES_REG=ROLES_REG, ROLES_CAL=ROLES_CAL, EDITABLES=EDITABLES, **ctx)

    @app.get("/control-cambios/{form_id}/editar", response_class=HTMLResponse)
    def cc_editar(req: Request, form_id: int):
        u = m.require(req, *ROLES_REG)
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] not in EDITABLES:
                raise HTTPException(400, "Solo se edita un formulario en borrador o devuelto")
            ctx = contexto(c, f)
        return m.view(req, "cc_form.html", **ctx)

    @app.post("/control-cambios/{form_id}/editar")
    async def cc_actualizar(req: Request, form_id: int):
        u = m.require(req, *ROLES_REG)
        fd = await req.form()
        m.csrf(req, fd.get("csrf_token"))
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] not in EDITABLES:
                raise HTTPException(400, "Solo se edita un formulario en borrador o devuelto")
            guardar_datos(c, form_id, fd, c.execute("SELECT id FROM countries").fetchall())
            m.audit(c, u["id"], "EDITAR", "ControlCambiosArte", form_id)
        return m.go(f"/control-cambios/{form_id}")

    # ---------- flujo ----------
    @app.post("/control-cambios/{form_id}/enviar")
    def cc_enviar(req: Request, form_id: int, csrf_token: str = Form(...)):
        u = m.require(req, *ROLES_REG)
        m.csrf(req, csrf_token)
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] not in EDITABLES:
                raise HTTPException(400, "El formulario ya fue enviado")
            d = json.loads(f["datos"] or "{}")
            if not d.get("version_propuesta") or not d.get("descripcion"):
                raise HTTPException(400, "Complete la versión propuesta y la descripción antes de enviar")
            if not c.execute("SELECT 1 FROM cc_afectaciones WHERE form_id=?", (form_id,)).fetchone():
                raise HTTPException(400, "Registre al menos una afectación")
            if not c.execute("SELECT 1 FROM cc_paises WHERE form_id=?", (form_id,)).fetchone():
                raise HTTPException(400, "Indique al menos un país")
            c.execute("UPDATE cc_forms SET estado='En revisión de Calidad', updated_at=? WHERE id=?", (m.utc(), form_id))
            firma(c, form_id, "Envío a Aseguramiento de Calidad", u, "Enviado")
            m.audit(c, u["id"], "ENVIAR", "ControlCambiosArte", form_id)
        return m.go(f"/control-cambios/{form_id}")

    @app.post("/control-cambios/{form_id}/revisar")
    def cc_revisar(req: Request, form_id: int, csrf_token: str = Form(...), decision: str = Form(...), observaciones: str = Form("")):
        u = m.require(req, *ROLES_CAL)
        m.csrf(req, csrf_token)
        if decision not in ("Aprobado", "Devuelto"):
            raise HTTPException(400, "Decisión inválida")
        if decision == "Devuelto" and not observaciones.strip():
            raise HTTPException(400, "Indique las correcciones")
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] != "En revisión de Calidad":
                raise HTTPException(400, "El formulario no está en revisión")
            c.execute("UPDATE cc_forms SET estado=?, updated_at=? WHERE id=?", (decision, m.utc(), form_id))
            firma(c, form_id, "Revisión del formulario (Aseguramiento de Calidad)", u, decision, observaciones.strip())
            m.audit(c, u["id"], "REVISAR", "ControlCambiosArte", form_id, new={"decision": decision})
        return m.go(f"/control-cambios/{form_id}")

    @app.post("/control-cambios/{form_id}/prueba/{ronda}")
    async def cc_prueba(req: Request, form_id: int, ronda: int):
        """Lista de chequeo de la prueba de impresión (XXX-001-F02): 1.ª revisión Regulatorios, 2.ª Calidad."""
        if ronda not in RONDAS:
            raise HTTPException(404, "Ronda inválida")
        u = m.require(req, *(ROLES_REG if ronda == 1 else ROLES_CAL))
        fd = await req.form()
        m.csrf(req, fd.get("csrf_token"))
        resultado = (fd.get("resultado") or "").strip()
        if resultado not in RONDAS[ronda][1]:
            raise HTTPException(400, "Resultado inválido")
        resp = {}
        for i in range(1, len(CHECKLIST) + 1):
            v = (fd.get(f"item_{i}") or "").strip()
            if v not in ("Sí", "No", "N/A"):
                raise HTTPException(400, f"Responda el punto {i} de la lista")
            resp[str(i)] = v
        obs = (fd.get("observaciones") or "").strip()[:1000]
        if "No" in resp.values() and resultado != "Devuelta":
            raise HTTPException(400, "Si algún punto es «No», la prueba debe devolverse")
        if resultado == "Devuelta" and not obs:
            raise HTTPException(400, "Indique las correcciones en observaciones")
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] != "Aprobado":
                raise HTTPException(400, "La prueba de impresión se revisa después de aprobado el formulario")
            if ronda == 2:
                r1 = c.execute("SELECT resultado FROM cc_prueba WHERE form_id=? AND ronda=1 ORDER BY id DESC LIMIT 1", (form_id,)).fetchone()
                if not r1 or r1["resultado"] != "Conforme":
                    raise HTTPException(400, "Primero debe estar conforme la primera revisión de Asuntos Regulatorios")
            c.execute("""INSERT INTO cc_prueba(form_id,ronda,fabricante,respuestas,resultado,observaciones,user_id,fecha)
                         VALUES(?,?,?,?,?,?,?,?)""", (form_id, ronda, (fd.get("fabricante") or "").strip()[:200],
                                                      json.dumps(resp), resultado, obs, u["id"], m.utc()))
            firma(c, form_id, f"Prueba de impresión: {RONDAS[ronda][0]}", u, resultado, obs)
            m.audit(c, u["id"], "PRUEBA_IMPRESION", "ControlCambiosArte", form_id, new={"ronda": ronda, "resultado": resultado})
        return m.go(f"/control-cambios/{form_id}")

    @app.get("/control-cambios/{form_id}/prueba/imprimir", response_class=HTMLResponse)
    def cc_prueba_imprimir(req: Request, form_id: int):
        u = m.require(req)
        with m.conn() as c:
            f = cargar(c, u, form_id)
            ctx = contexto(c, f)
        return m.templates.TemplateResponse(req, "cc_prueba_imprimir.html", {"u": u,
                                            "impreso": datetime.now().strftime("%d/%m/%Y %H:%M"), **ctx})

    @app.post("/control-cambios/{form_id}/rechazar")
    def cc_rechazar(req: Request, form_id: int, csrf_token: str = Form(...), motivo: str = Form(...)):
        """Rechazo de la autoridad reguladora: cierra el formulario sin crear versión."""
        u = m.require(req, *ROLES_REG)
        m.csrf(req, csrf_token)
        if not motivo.strip():
            raise HTTPException(400, "Indique el motivo")
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] in ("Cerrado", "Rechazado"):
                raise HTTPException(400, "El formulario ya está cerrado")
            c.execute("UPDATE cc_forms SET estado='Rechazado', updated_at=? WHERE id=?", (m.utc(), form_id))
            firma(c, form_id, "Cierre por rechazo de la autoridad", u, "Rechazado", motivo.strip())
            m.audit(c, u["id"], "RECHAZAR", "ControlCambiosArte", form_id, new={"motivo": motivo.strip()[:300]})
        return m.go(f"/control-cambios/{form_id}")

    @app.post("/control-cambios/{form_id}/cerrar")
    async def cc_cerrar(req: Request, form_id: int, csrf_token: str = Form(...), revision: str = Form(...),
                        pdf: UploadFile = File(...), confirmar: str = Form("")):
        """Firma y cierre: crea la revisión en Medigray y actualiza el estado por país."""
        u = m.require(req, *ROLES_REG)
        m.csrf(req, csrf_token)
        revision = revision.strip()[:40]
        if not revision or confirmar != "si":
            raise HTTPException(400, "Indique la revisión y confirme el cierre")
        if not (pdf.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Adjunte el arte firmado en PDF")
        with m.conn() as c:
            f = cargar(c, u, form_id)
            if f["estado"] != "Aprobado":
                raise HTTPException(400, "Solo se cierra un formulario aprobado por Aseguramiento de Calidad")
            m.module_access(u, f["company_id"], "versiones")
        temp = m.FILES / f"temp_{uuid.uuid4().hex}"
        sha, total = hashlib.sha256(), 0
        try:
            with temp.open("wb") as out:
                head = await pdf.read(5)
                if head != b"%PDF-":
                    raise HTTPException(400, "El archivo no tiene cabecera PDF válida")
                out.write(head); sha.update(head); total += len(head)
                while True:
                    block = await pdf.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > 20 * 1024 * 1024:
                        raise HTTPException(413, "Máximo 20 MB")
                    out.write(block); sha.update(block)
            stored = f"{uuid.uuid4().hex}.pdf"
            hoy = date.today().isoformat()
            with m.conn() as c:
                f = cargar(c, u, form_id)
                if f["estado"] != "Aprobado":
                    raise HTTPException(400, "El formulario cambió de estado")
                if c.execute("SELECT 1 FROM versions WHERE art_id=? AND revision=?", (f["art_id"], revision)).fetchone():
                    raise HTTPException(400, "Esa revisión ya existe para este arte")
                cur = c.execute("""INSERT INTO versions(art_id,change_id,revision,filename,stored_name,sha256,created_by,created_at)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (f["art_id"], None, revision, Path(pdf.filename).name[:200], stored, sha.hexdigest(), u["id"], m.utc()))
                vid = cur.lastrowid
                resumen = []
                for p in c.execute("SELECT * FROM cc_paises WHERE form_id=?", (form_id,)).fetchall():
                    est = p["estado_regulatorio"]
                    if est not in EST_REG:
                        continue
                    local = p["version_aplica"] or revision
                    nota = f"{f['numero']}" + (f" · trámite {p['tramite']}" if p["tramite"] else "")
                    if est in ("aprobado", "interno"):
                        anteriores = c.execute("""SELECT cv.id FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                            WHERE v.art_id=? AND cv.country_id=? AND cv.status='Vigente Oficial'""", (f["art_id"], p["country_id"])).fetchall()
                        for a in anteriores:
                            if p["fin_vigencia_anterior"]:
                                c.execute("UPDATE country_versions SET status='Vigente Transitorio', transition_until=?, notes=? WHERE id=?",
                                          (p["fin_vigencia_anterior"], f"Sustituida por {revision} ({f['numero']})", a["id"]))
                            else:
                                c.execute("UPDATE country_versions SET status='Inactivo/Obsoleto', notes=? WHERE id=?",
                                          (f"Sustituida por {revision} ({f['numero']})", a["id"]))
                        c.execute("""INSERT INTO country_versions(version_id,country_id,local_version,status,approval_date,valid_from,notes)
                                     VALUES(?,?,?,'Vigente Oficial',?,?,?)""",
                                  (vid, p["country_id"], local, p["fecha_tramite"] or hoy, hoy, nota))
                        resumen.append(p["country_id"])
                    else:
                        c.execute("""INSERT INTO country_versions(version_id,country_id,local_version,status,notes)
                                     VALUES(?,?,?,'Pendiente',?)""", (vid, p["country_id"], local, nota))
                os.replace(temp, m.FILES / stored)
                c.execute("UPDATE cc_forms SET estado='Cerrado', version_id=?, updated_at=? WHERE id=?", (vid, m.utc(), form_id))
                firma(c, form_id, "Firma del arte y cierre (Asuntos Regulatorios)", u, "Cerrado", f"Revisión {revision} registrada")
                m.audit(c, u["id"], "CERRAR", "ControlCambiosArte", form_id, new={"version_id": vid, "revision": revision, "paises_vigentes": resumen})
        finally:
            if temp.exists():
                temp.unlink()
            await pdf.close()
        return m.go(f"/control-cambios/{form_id}")

    # ---------- impresión (formato oficial) ----------
    @app.get("/control-cambios/{form_id}/imprimir", response_class=HTMLResponse)
    def cc_imprimir(req: Request, form_id: int):
        u = m.require(req)
        with m.conn() as c:
            f = cargar(c, u, form_id)
            ctx = contexto(c, f)
            m.audit(c, u["id"], "IMPRIMIR", "ControlCambiosArte", form_id)
        etapas = {}
        for fi in ctx["firmas"]:
            etapas[fi["etapa"]] = fi  # la última firma de cada etapa
        return m.templates.TemplateResponse(req, "cc_imprimir.html", {"u": u, "etapas": etapas,
                                            "impreso": datetime.now().strftime("%d/%m/%Y %H:%M"), **ctx})
