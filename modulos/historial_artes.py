"""Pantalla /artes/historial: todas las revisiones de artes por empresa y país."""
from fastapi import Request
from fastapi.responses import HTMLResponse


def registrar(app, conn, require, view, company_ids):
    @app.get("/artes/historial", response_class=HTMLResponse, name="historial_artes")
    def historial_artes(req: Request, q: str = "", company: int = 0, country: int = 0):
        u = require(req)
        permitidas = list(company_ids(u))
        q = q.strip()[:120]
        if company and company not in permitidas:
            company = 0
        empresas = [company] if company else permitidas
        with conn() as c:
            companies = c.execute(
                "SELECT id,name FROM companies WHERE id IN (%s) ORDER BY name"
                % ",".join("?" * len(permitidas)), permitidas).fetchall() if permitidas else []
            countries = c.execute("SELECT id,name FROM countries ORDER BY name").fetchall()
            items = []
            if empresas:
                like = "%" + q + "%"
                items = c.execute(
                    """SELECT a.id AS art_id, a.sap_code, a.master_code, a.product,
                              co.name AS company_name, v.id AS version_id, v.revision,
                              p.name AS country_name, cv.local_version, cv.status
                       FROM versions v
                       JOIN arts a ON a.id = v.art_id
                       JOIN companies co ON co.id = a.company_id
                       LEFT JOIN country_versions cv ON cv.version_id = v.id
                       LEFT JOIN countries p ON p.id = cv.country_id
                       WHERE a.company_id IN (%s)
                         AND (? = 0 OR cv.country_id = ?)
                         AND (? = '' OR a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
                       ORDER BY a.product COLLATE NOCASE, v.id DESC, p.name
                       LIMIT 2000""" % ",".join("?" * len(empresas)),
                    (*empresas, country, country, q, like, like, like)).fetchall()
        return view(req, "historial_artes.html", items=items, companies=companies,
                    countries=countries, q=q, company=company, country=country)

    # Debe ir antes de /artes/{art_id}; si no, "historial" se toma como número de arte.
    ruta = app.router.routes.pop()
    app.router.routes.insert(0, ruta)
