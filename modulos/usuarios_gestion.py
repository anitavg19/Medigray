"""Pantalla Usuarios mejorada.

- Fecha de creación del perfil y quién lo creó.
- Eliminar usuarios sin actividad (los que ya tienen registros se desactivan).
- Historial de cambios de usuarios con el correo de quien hizo cada cambio.
- Ver u ocultar la contraseña al escribirla.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse

CR = timezone(timedelta(hours=-6))  # Costa Rica no usa horario de verano
ACCIONES = {
    "BOOTSTRAP": "Primer administrador (consola)",
    "BOOTSTRAP_ENV": "Primer administrador (configuración)",
    "CREAR_USUARIO": "Creó el usuario",
    "CAMBIAR_ACCESO": "Cambió el acceso",
    "ELIMINAR_USUARIO": "Eliminó el usuario",
    "RESTABLECER_PASSWORD_CLI": "Cambió la contraseña (consola)",
}


def fecha_local(iso):
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(CR).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso[:16]


def register(m):
    app = m.app

    def referencias(c, uid):
        """Registros que apuntan al usuario en cualquier tabla con llave hacia users."""
        total = 0
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            if t in ("users", "user_companies"):
                continue
            for fk in c.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
                if fk[2] == "users":
                    total += c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{fk[3]}"=?', (uid,)).fetchone()[0]
        return total

    @app.get("/usuarios", response_class=HTMLResponse)
    def usuarios(req: Request):
        u = m.require(req, "Administrador")
        mias = m.company_ids(u)
        marcas = ",".join("?" * len(mias)) or "NULL"
        with m.conn() as c:
            filas = c.execute(f"""SELECT DISTINCT us.id, us.email, us.role, us.active, us.created_at FROM users us
                JOIN user_companies uc ON uc.user_id=us.id WHERE uc.company_id IN ({marcas}) ORDER BY us.id""", mias).fetchall()
            companies = c.execute(f"SELECT * FROM companies WHERE id IN ({marcas}) ORDER BY name", mias).fetchall()
            empresas = {}
            for r in c.execute("SELECT uc.user_id, co.name FROM user_companies uc JOIN companies co ON co.id=uc.company_id"):
                empresas.setdefault(r["user_id"], []).append(r["name"])
            visibles = {r["id"] for r in filas}
            eventos = c.execute("""SELECT au.*, ac.email actor_email, tg.email target_email FROM audit au
                LEFT JOIN users ac ON ac.id=au.actor_id LEFT JOIN users tg ON tg.id=au.entity_id
                WHERE au.entity='Usuario' ORDER BY au.id DESC LIMIT 1000""").fetchall()
        rows, historial, creado_por = [], [], {}
        for e in eventos:
            nuevo = json.loads(e["new_value"]) if e["new_value"] else {}
            viejo = json.loads(e["old_value"]) if e["old_value"] else {}
            correo = nuevo.get("email") or viejo.get("email") or e["target_email"] or f"usuario #{e['entity_id']}"
            if e["entity_id"] not in visibles and e["actor_id"] not in visibles:
                continue
            if e["action"] == "CAMBIAR_ACCESO":
                detalle = "Activado" if nuevo.get("active") == 1 else "Desactivado"
            elif e["action"] == "CREAR_USUARIO":
                detalle = f"Rol: {nuevo.get('role', '')}"
            elif e["action"] == "ELIMINAR_USUARIO":
                detalle = f"Rol: {viejo.get('role', '')}"
            else:
                detalle = ""
            historial.append({"fecha": fecha_local(e["created_at"]), "accion": ACCIONES.get(e["action"], e["action"]),
                              "usuario": correo.lower(), "por": e["actor_email"] or "—", "detalle": detalle})
            if e["action"] in ("CREAR_USUARIO", "BOOTSTRAP", "BOOTSTRAP_ENV") and e["entity_id"] in visibles:
                creado_por.setdefault((e["entity_id"], correo.lower()),
                                      e["actor_email"] if e["action"] == "CREAR_USUARIO" else "Consola / configuración")
        for r in filas:
            x = dict(r)
            x["creado"] = fecha_local(r["created_at"])
            x["creado_por"] = creado_por.get((r["id"], r["email"].lower()), "—")
            x["empresas"] = ", ".join(sorted(empresas.get(r["id"], [])))
            rows.append(x)
        return m.view(req, "usuarios_gestion.html", rows=rows, roles=m.ROLES, companies=companies, historial=historial)

    @app.post("/usuarios/{uid}/eliminar")
    def eliminar_usuario(req: Request, uid: int, csrf_token: str = Form(...)):
        u = m.require(req, "Administrador")
        m.csrf(req, csrf_token)
        if uid == u["id"]:
            raise HTTPException(400, "No puede eliminar su propio usuario")
        with m.conn() as c:
            r = m.ensure(c, "users", uid)
            empresas = {x["company_id"] for x in c.execute("SELECT company_id FROM user_companies WHERE user_id=?", (uid,))}
            if empresas and not set(m.company_ids(u)).intersection(empresas):
                raise HTTPException(403, "Usuario de otra empresa")
            if r["role"] == "Administrador" and r["active"]:
                if not c.execute("SELECT COUNT(*) FROM users WHERE role='Administrador' AND active=1 AND id<>?", (uid,)).fetchone()[0]:
                    raise HTTPException(400, "No se puede eliminar el único administrador activo")
            n = referencias(c, uid)
            if n:
                raise HTTPException(400, f"Este usuario ya tiene {n} registros en el sistema. "
                                         "Para no perder la trazabilidad, desactívelo en lugar de eliminarlo.")
            c.execute("DELETE FROM user_companies WHERE user_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))
            m.audit(c, u["id"], "ELIMINAR_USUARIO", "Usuario", uid, old={"email": r["email"], "role": r["role"]})
        return m.go("/usuarios")

    # La pantalla nueva debe atender /usuarios antes que la original.
    for ruta in [x for x in app.router.routes if getattr(x, "path", "") == "/usuarios" and "GET" in getattr(x, "methods", set())
                 and getattr(x, "endpoint", None) is usuarios]:
        app.router.routes.remove(ruta)
        app.router.routes.insert(0, ruta)
