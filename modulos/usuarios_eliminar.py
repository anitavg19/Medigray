"""Eliminar usuarios desde la pantalla Usuarios.

Solo borra usuarios que no han hecho nada en el sistema (ningún registro los
menciona como autor). Si ya tienen actividad, se pide desactivarlos para no
perder la trazabilidad de la auditoría.
"""
from fastapi import Request, Form, HTTPException


def register(m):
    app = m.app

    def referencias(c, uid):
        """Cuenta los registros que apuntan al usuario, en cualquier tabla con llave hacia users."""
        total = 0
        tablas = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tablas:
            if t in ("users", "user_companies"):
                continue
            for fk in c.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
                if fk[2] == "users":
                    total += c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{fk[3]}"=?', (uid,)).fetchone()[0]
        return total

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
                otros = c.execute("SELECT COUNT(*) FROM users WHERE role='Administrador' AND active=1 AND id<>?", (uid,)).fetchone()[0]
                if not otros:
                    raise HTTPException(400, "No se puede eliminar el único administrador activo")
            n = referencias(c, uid)
            if n:
                raise HTTPException(400, f"Este usuario ya tiene {n} registros en el sistema. "
                                         "Para no perder la trazabilidad, desactívelo en lugar de eliminarlo.")
            c.execute("DELETE FROM user_companies WHERE user_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))
            m.audit(c, u["id"], "ELIMINAR_USUARIO", "Usuario", uid, old={"email": r["email"], "role": r["role"]})
        return m.go("/usuarios")
