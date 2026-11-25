"""Extensión aditiva para Control de Artes: homologación regional.
No altera la vigencia de country_versions ni las aprobaciones existentes.
"""
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse

ROLES_EDITAR = ('Administrador', 'Regulatorios')
ESTADOS = ('Homologado', 'Diferente', 'Pendiente')
CAUSAS = ('Regulatoria', 'Comercial', 'Técnica', 'Otra', 'Sin definir')

def montar_homologacion(app, conn, require, csrf, view, go, company_access, audit, utc):
    """Registrar rutas usando los controles de autenticación existentes del proyecto."""
    router = APIRouter()
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS regional_homologacion (
            id INTEGER PRIMARY KEY,
            art_id INTEGER NOT NULL REFERENCES arts(id),
            country_id INTEGER NOT NULL REFERENCES countries(id),
            design_code TEXT NOT NULL DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'Pendiente' CHECK (estado IN ('Homologado','Diferente','Pendiente')),
            tipo_diferencia TEXT NOT NULL DEFAULT 'Sin definir',
            motivo TEXT NOT NULL DEFAULT '',
            posibilidad_unificacion TEXT NOT NULL DEFAULT 'Por evaluar',
            evidencia_url TEXT NOT NULL DEFAULT '',
            responsable TEXT NOT NULL DEFAULT '',
            updated_by INTEGER NOT NULL REFERENCES users(id),
            updated_at TEXT NOT NULL,
            UNIQUE(art_id,country_id)
        );
        CREATE INDEX IF NOT EXISTS regional_homologacion_arte_idx ON regional_homologacion(art_id);
        ''')

    def empresa(req, u):
        permitidas = sorted(set(int(x) for x in __company_ids(u)))
        actual = req.session.get('empresa_actual')
        try: actual = int(actual)
        except (TypeError, ValueError): actual = None
        if actual not in permitidas:
            actual = permitidas[0] if permitidas else None
        if actual is None: raise HTTPException(403, 'Usuario sin empresas asignadas')
        company_access(u, actual)
        return actual

    def __company_ids(u):
        with conn() as c:
            return [r['company_id'] for r in c.execute(
                'SELECT company_id FROM user_companies WHERE user_id=?', (u['id'],)).fetchall()]

    def check_art(c, u, art_id, cid):
        row = c.execute('SELECT * FROM arts WHERE id=? AND company_id=?', (art_id, cid)).fetchone()
        if not row: raise HTTPException(404, 'Arte inexistente o no perteneciente a la empresa seleccionada')
        company_access(u, row['company_id'])
        return row

    def link_ok(value):
        if not value: return True
        parts = urlparse(value)
        return parts.scheme == 'https' and bool(parts.netloc) and not parts.username and not parts.password

    def estado_paises(c, art_id):
        # El último estado registrado es informativo, nunca sustituye una aprobación sanitaria.
        return c.execute('''
        SELECT p.id country_id,p.name country_name,h.design_code,h.estado,h.tipo_diferencia,
               h.motivo,h.posibilidad_unificacion,h.evidencia_url,h.responsable,h.updated_at,
               (SELECT COUNT(*) FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                  WHERE v.art_id=? AND cv.country_id=p.id AND cv.status='Vigente Transitorio') AS n_transitorios,
               (SELECT cv.local_version FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                  WHERE v.art_id=? AND cv.country_id=p.id AND cv.status='Vigente Oficial'
                  ORDER BY cv.id DESC LIMIT 1) AS version_principal,
               (SELECT cv.status FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                  WHERE v.art_id=? AND cv.country_id=p.id
                  ORDER BY cv.id DESC LIMIT 1) AS ultimo_estado
        FROM countries p LEFT JOIN regional_homologacion h ON h.country_id=p.id AND h.art_id=?
        ORDER BY p.name
        ''', (art_id,art_id,art_id,art_id)).fetchall()

    @router.get('/homologacion', response_class=HTMLResponse)
    def homologacion(req: Request, q: str = ''):
        u = require(req)
        cid = empresa(req,u)
        with conn() as c:
            lista = c.execute('''
            SELECT a.id,a.master_code,a.sap_code,a.product,a.packaging_type,
              COUNT(h.id) AS paises_evaluados,
              SUM(CASE WHEN h.estado='Homologado' THEN 1 ELSE 0 END) AS homologados,
              SUM(CASE WHEN h.estado='Diferente' THEN 1 ELSE 0 END) AS diferentes,
              SUM(CASE WHEN h.estado='Pendiente' THEN 1 ELSE 0 END) AS pendientes
            FROM arts a LEFT JOIN regional_homologacion h ON h.art_id=a.id
            WHERE a.company_id=? AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
            GROUP BY a.id ORDER BY a.product,a.id LIMIT 250
            ''', (cid, f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
        return view(req,'homologacion.html', lista=lista, q=q)

    @router.get('/homologacion/{art_id}', response_class=HTMLResponse)
    def homologacion_detalle(req: Request, art_id: int):
        u = require(req)
        cid = empresa(req,u)
        with conn() as c:
            art = check_art(c,u,art_id,cid)
            rows = estado_paises(c,art_id)
        # No interpretar códigos idénticos como equivalencia aprobada sin una decisión de Regulatorios.
        grupos = {}
        for r in rows:
            if r['estado']=='Homologado' and r['design_code']:
                grupos.setdefault(r['design_code'], []).append(r['country_name'])
        return view(req,'homologacion_detalle.html',art=art,paises=rows,grupos=grupos,
                    puede_editar=u['role'] in ROLES_EDITAR,estados=ESTADOS,causas=CAUSAS)

    @router.post('/homologacion/{art_id}/guardar')
    def homologacion_guardar(req: Request, art_id: int, csrf_token: str = Form(...),
              country_id: int = Form(...), design_code: str = Form(''),
              estado: str = Form('Pendiente'), tipo_diferencia: str = Form('Sin definir'),
              motivo: str = Form(''), posibilidad_unificacion: str = Form('Por evaluar'),
              evidencia_url: str = Form(''), responsable: str = Form('')):
        u = require(req,*ROLES_EDITAR)
        csrf(req, csrf_token)
        cid = empresa(req,u)
        design_code=design_code.strip(); motivo=motivo.strip(); evidencia_url=evidencia_url.strip()
        if estado not in ESTADOS or tipo_diferencia not in CAUSAS: raise HTTPException(400,'Clasificación inválida')
        if len(design_code)>100 or len(motivo)>2000 or len(posibilidad_unificacion)>120 or len(responsable)>120:
            raise HTTPException(400,'Uno de los textos excede el máximo permitido')
        if len(evidencia_url)>1500 or not link_ok(evidencia_url): raise HTTPException(400,'La evidencia requiere un enlace HTTPS válido')
        if estado=='Homologado' and not design_code: raise HTTPException(400,'Para homologar, registre el código de diseño compartido')
        if estado=='Diferente' and not motivo: raise HTTPException(400,'Documente el motivo de la diferencia')
        with conn() as c:
            check_art(c,u,art_id,cid)
            if not c.execute('SELECT 1 FROM countries WHERE id=?',(country_id,)).fetchone():
                raise HTTPException(400,'País desconocido')
            old = c.execute('SELECT * FROM regional_homologacion WHERE art_id=? AND country_id=?', (art_id,country_id)).fetchone()
            c.execute('''INSERT INTO regional_homologacion
               (art_id,country_id,design_code,estado,tipo_diferencia,motivo,posibilidad_unificacion,evidencia_url,responsable,updated_by,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(art_id,country_id) DO UPDATE SET
               design_code=excluded.design_code,estado=excluded.estado,tipo_diferencia=excluded.tipo_diferencia,
               motivo=excluded.motivo,posibilidad_unificacion=excluded.posibilidad_unificacion,
               evidencia_url=excluded.evidencia_url,responsable=excluded.responsable,
               updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
               (art_id,country_id,design_code,estado,tipo_diferencia,motivo,
                posibilidad_unificacion.strip() or 'Por evaluar',evidencia_url,responsable.strip(),u['id'],utc()))
            new={'country_id':country_id,'design_code':design_code,'estado':estado,'tipo_diferencia':tipo_diferencia,
                 'motivo':motivo,'posibilidad_unificacion':posibilidad_unificacion,'evidencia_url':evidencia_url,
                 'responsable':responsable}
            audit(c,u['id'],'HOMOLOGACION_REGIONAL','Arte',art_id,
                  old=dict(old) if old else None,new=new)
        return go(f'/homologacion/{art_id}#pais-{country_id}')

    app.include_router(router)
