"""Pantalla de artes vigentes y seguimiento, basada en las tablas existentes."""
from datetime import date
from calendar import monthrange
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

ACTIVE = ("Vigente Oficial", "Vigente Transitorio")

def _add_months(day, months):
    y = day.year + (day.month - 1 + months) // 12
    m = (day.month - 1 + months) % 12 + 1
    return date(y, m, min(day.day, monthrange(y, m)[1]))

def register(app, conn, require, view, company_ids, module_access):
    @app.get("/artes/vigentes", response_class=HTMLResponse, name="listado_artes_vigentes")
    def listado_artes_vigentes(req: Request, q: str = "", country: int = 0, filtro: str = "todos"):
        u = require(req)
        permitted = company_ids(u)
        company_id = req.session.get("empresa_actual")
        if company_id not in permitted:
            company_id = permitted[0] if permitted else None
            if company_id is not None:
                req.session["empresa_actual"] = company_id
        if company_id is None:
            raise HTTPException(403, "Sin empresa asignada")
        module_access(u, company_id, "artes")
        if filtro not in ("todos", "principal", "transitorio", "3m", "6m", "vencido", "sin_fecha"):
            raise HTTPException(400, "Filtro inválido")
        q = q.strip()[:120]
        with conn() as c:
            countries = c.execute(
                "SELECT id,name FROM countries ORDER BY name"
            ).fetchall()
            # No se mezclan registros de empresas, incluso si el usuario puede ver ambas.
            rows = c.execute("""
                SELECT cv.id AS cv_id, cv.country_id, cv.local_version, cv.status,
                       cv.approval_date, cv.valid_from, cv.transition_until, cv.notes,
                       v.id AS version_id, v.revision, v.filename,
                       a.id AS art_id, a.master_code, a.sap_code, a.product,
                       a.packaging_type, p.name AS country
                FROM country_versions cv
                JOIN versions v ON v.id = cv.version_id
                JOIN arts a ON a.id = v.art_id
                JOIN countries p ON p.id = cv.country_id
                WHERE a.company_id = ?
                  AND cv.status IN ('Vigente Oficial','Vigente Transitorio')
                  AND (? = 0 OR cv.country_id = ?)
                  AND (? = '' OR a.sap_code LIKE ? OR a.master_code LIKE ?
                       OR a.product LIKE ? OR cv.local_version LIKE ? OR p.name LIKE ?)
                ORDER BY a.product COLLATE NOCASE, p.name COLLATE NOCASE,
                         CASE WHEN cv.status = 'Vigente Oficial' THEN 0 ELSE 1 END,
                         cv.id DESC
                LIMIT 1000
            """, (company_id, country, country, q,
                  *(["%" + q + "%"] * 5))).fetchall()
            docs = {r["art_id"]:r["url"] for r in c.execute(
                """SELECT art_id,url FROM art_document_links
                   WHERE company_id=? ORDER BY id""", (company_id,)
            ).fetchall()}
            # La homologación NO se deduce del nombre de revisión:
            # debe utilizar la evaluación explícita del módulo /homologacion.
        today = date.today()
        three = _add_months(today, 3)
        six = _add_months(today, 6)
        items = []
        metrics = dict(principales=0, transitorios=0, tres_meses=0,
                       seis_meses=0, vencidos=0, sin_fecha=0)
        for raw in rows:
            r = dict(raw)
            r["drive_link"] = docs.get(r["art_id"])
            r["alerta"] = ""
            r["dias_restantes"] = None
            if r["status"] == "Vigente Oficial":
                metrics["principales"] += 1
                r["alerta"] = "Principal"
            else:
                metrics["transitorios"] += 1
                if not r["transition_until"]:
                    metrics["sin_fecha"] += 1
                    r["alerta"] = "Sin fecha definida"
                else:
                    try:
                        end = date.fromisoformat(r["transition_until"])
                        r["dias_restantes"] = (end - today).days
                        if end < today:
                            metrics["vencidos"] += 1
                            r["alerta"] = "Plazo vencido — revisar"
                        elif end <= three:
                            metrics["tres_meses"] += 1
                            r["alerta"] = "Vence en 3 meses"
                        elif end <= six:
                            metrics["seis_meses"] += 1
                            r["alerta"] = "Vence en 6 meses"
                        else:
                            r["alerta"] = "En agotamiento"
                    except ValueError:
                        metrics["sin_fecha"] += 1
                        r["alerta"] = "Fecha inválida — revisar"
            items.append(r)
        key = {
            "principal": lambda x: x["status"] == "Vigente Oficial",
            "transitorio": lambda x: x["status"] == "Vigente Transitorio",
            "3m": lambda x: x["alerta"] == "Vence en 3 meses",
            "6m": lambda x: x["alerta"] == "Vence en 6 meses",
            "vencido": lambda x: x["alerta"].startswith("Plazo vencido"),
            "sin_fecha": lambda x: x["alerta"].startswith(("Sin fecha","Fecha inválida")),
        }
        shown = [x for x in items if key[filtro](x)] if filtro in key else items
        return view(req, "artes_vigentes_dashboard.html",
                    rows=shown, metrics=metrics, q=q, country=country,
                    countries=countries, filtro=filtro, total=len(items),
                    hoy=today.isoformat(), fecha_tres=three.isoformat(),
                    fecha_seis=six.isoformat())
