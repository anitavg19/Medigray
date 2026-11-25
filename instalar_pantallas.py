#!/usr/bin/env python3
"""Agrega consultas (lectura) de artes vigentes e historial a Medigray v2.
No modifica SQLite ni archivos adjuntos.
"""
from pathlib import Path
from datetime import datetime
import shutil, sys

ROOT=Path(__file__).resolve().parent
app=ROOT/'app.py'
base=ROOT/'templates'/'base.html'
if not app.exists() or not base.exists():
    sys.exit('ERROR: Coloque este script en la misma carpeta que app.py y templates/.')
source=app.read_text(encoding='utf-8')
layout=base.read_text(encoding='utf-8')
required=['def conn():','def require(', 'def view(', 'def company_ids(', 'CREATE TABLE IF NOT EXISTS country_versions']
if not all(x in source for x in required):
    sys.exit('ERROR: La estructura de app.py es distinta. No se cambió nada.')
if '{% if u %}' not in layout or '<nav>' not in layout or '</nav>' not in layout:
    sys.exit('ERROR: base.html no parece compatible. No se cambió nada.')
if "@app.get('/artes/vigentes')" in source or "@app.get('/artes/historial')" in source:
    sys.exit('Las rutas ya existen. No se realizaron cambios.')
anchor="@app.get('/artes/nuevo')"
if source.count(anchor)!=1:
    sys.exit('ERROR: No se encontró la ubicación segura para añadir rutas. Sin cambios.')

routes="""\n@app.get('/artes/vigentes', response_class=HTMLResponse)
def artes_vigentes_pagina(req: Request, q: str = '', company: int = 0, country: int = 0):
    u = require(req)
    accessible = company_ids(u)
    if company and company not in accessible:
        raise HTTPException(403, 'Sin acceso a esta empresa')
    with conn() as c:
        companies = c.execute('SELECT id,name FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY name', (u['id'],)).fetchall()
        countries = c.execute('SELECT id,name FROM countries ORDER BY name').fetchall()
        items = c.execute(\"\"\"SELECT a.id art_id, a.master_code, a.sap_code, a.product, a.packaging_type,
                                    co.name company_name, p.name country_name, v.id version_id,
                                    v.revision, cv.local_version, cv.status, cv.valid_from, cv.transition_until
                             FROM country_versions cv
                             JOIN versions v ON v.id=cv.version_id
                             JOIN arts a ON a.id=v.art_id
                             JOIN companies co ON co.id=a.company_id
                             JOIN countries p ON p.id=cv.country_id
                             WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                               AND (?=0 OR a.company_id=?) AND (?=0 OR cv.country_id=?)
                               AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
                               AND cv.status='Vigente Oficial'
                               AND (cv.valid_from IS NULL OR cv.valid_from='' OR cv.valid_from<=date('now'))
                             ORDER BY co.name, a.sap_code, p.name LIMIT 500\"\"\",
                          (u['id'],company,company,country,country,*[f'%{q}%']*3)).fetchall()
    return view(req, 'artes_vigentes.html', items=items, companies=companies, countries=countries, q=q, company=company, country=country)

@app.get('/artes/historial', response_class=HTMLResponse)
def historial_artes_pagina(req: Request, q: str = '', company: int = 0, country: int = 0):
    u = require(req)
    accessible = company_ids(u)
    if company and company not in accessible:
        raise HTTPException(403, 'Sin acceso a esta empresa')
    with conn() as c:
        companies = c.execute('SELECT id,name FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY name', (u['id'],)).fetchall()
        countries = c.execute('SELECT id,name FROM countries ORDER BY name').fetchall()
        items = c.execute(\"\"\"SELECT a.id art_id, a.master_code, a.sap_code, a.product, co.name company_name,
                                    v.id version_id, v.revision, v.created_at,
                                    p.name country_name, cv.local_version, cv.status
                             FROM versions v
                             JOIN arts a ON a.id=v.art_id
                             JOIN companies co ON co.id=a.company_id
                             LEFT JOIN country_versions cv ON cv.version_id=v.id
                             LEFT JOIN countries p ON p.id=cv.country_id
                             WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                               AND (?=0 OR a.company_id=?) AND (?=0 OR cv.country_id=?)
                               AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
                             ORDER BY a.id DESC, v.id DESC LIMIT 500\"\"\",
                          (u['id'],company,company,country,country,*[f'%{q}%']*3)).fetchall()
    return view(req, 'historial_artes.html', items=items, companies=companies, countries=countries, q=q, company=company, country=country)

"""
source=source.replace(anchor,routes+anchor,1)
needle='<a href="/alertas">◷ Alertas y pendientes</a>'
links='<a href="/artes/vigentes">✓ Artes vigentes</a>\n<a href="/artes/historial">◷ Historial de artes</a>\n'
if needle in layout:
    layout=layout.replace(needle,links+needle,1)
else:
    n=layout.find('<nav>')
    layout=layout[:n+5]+links+layout[n+5:]

vigentes="""{% extends 'base.html' %}{% block content %}
<h1>Artes vigentes</h1><p class="muted">Versión oficial actualmente efectiva por empresa y país. Para versiones transitorias consulte la ficha del arte.</p>
<section class="panel"><form method="get" class="grid">
<div><label>Buscar SAP, código o producto</label><input name="q" value="{{ q }}" placeholder="Ej. 10004567"></div>
<div><label>Empresa</label><select name="company"><option value="0">Todas las autorizadas</option>{% for c in companies %}<option value="{{ c.id }}" {% if company==c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select></div>
<div><label>País</label><select name="country"><option value="0">Todos</option>{% for c in countries %}<option value="{{ c.id }}" {% if country==c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select></div>
<div><button class="btn" type="submit">Buscar artes</button></div></form></section>
<section class="panel scroll"><table><thead><tr><th>SAP</th><th>Empresa</th><th>Producto</th><th>País</th><th>Versión</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>
{% for a in items %}<tr><td class="code">{{ a.sap_code }}</td><td>{{ a.company_name }}</td><td>{{ a.product }}</td><td>{{ a.country_name }}</td><td>{{ a.local_version or a.revision }}</td><td><span class="badge ok">{{ a.status }}</span></td><td><a href="/artes/{{ a.art_id }}">Ver expediente</a> · <a href="/documentos/{{ a.version_id }}">PDF</a> · <a href="/artes/{{ a.art_id }}/cambios/nuevo">Solicitar cambio</a></td></tr>{% else %}<tr><td colspan="7">No hay artes oficialmente vigentes con estos criterios. Las versiones pendientes o sin fecha de vigencia no aparecen aquí.</td></tr>{% endfor %}</tbody></table></section>
{% endblock %}
"""
historial="""{% extends 'base.html' %}{% block content %}
<h1>Historial de artes</h1><p class="muted">Incluye versiones vigentes, pendientes y anteriores. Ninguna revisión histórica se elimina por consultar esta pantalla.</p>
<section class="panel"><form method="get" class="grid">
<div><label>Buscar SAP, código o producto</label><input name="q" value="{{ q }}" placeholder="Ej. 10004567"></div>
<div><label>Empresa</label><select name="company"><option value="0">Todas las autorizadas</option>{% for c in companies %}<option value="{{ c.id }}" {% if company==c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select></div>
<div><label>País</label><select name="country"><option value="0">Todos</option>{% for c in countries %}<option value="{{ c.id }}" {% if country==c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select></div>
<div><button class="btn" type="submit">Buscar historial</button></div></form></section>
<section class="panel scroll"><table><thead><tr><th>Código SAP</th><th>Empresa</th><th>Producto</th><th>Revisión</th><th>País</th><th>Versión país</th><th>Estado</th><th>Documentos</th></tr></thead><tbody>
{% for a in items %}<tr><td class="code">{{ a.sap_code }}</td><td>{{ a.company_name }}</td><td>{{ a.product }}</td><td>{{ a.revision }}</td><td>{{ a.country_name or 'Sin asignar' }}</td><td>{{ a.local_version or '—' }}</td><td><span class="badge">{{ a.status or 'Sin asignar' }}</span></td><td><a href="/artes/{{ a.art_id }}">Expediente</a> · <a href="/documentos/{{ a.version_id }}">PDF de revisión</a></td></tr>{% else %}<tr><td colspan="8">Sin versiones registradas con estos criterios.</td></tr>{% endfor %}</tbody></table></section>
{% endblock %}
"""
# Verify compatibility before writing anything
compile(source, str(app), 'exec')
from jinja2 import Environment
for text in (layout,vigentes,historial): Environment().parse(text)
backup=ROOT/('respaldo_codigo_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir()
shutil.copy2(app,backup/'app.py')
shutil.copy2(base,backup/'base.html')
app.write_text(source,encoding='utf-8')
base.write_text(layout,encoding='utf-8')
(ROOT/'templates'/'artes_vigentes.html').write_text(vigentes,encoding='utf-8')
(ROOT/'templates'/'historial_artes.html').write_text(historial,encoding='utf-8')
print('INSTALACIÓN TERMINADA. Se crearon las dos pantallas y se actualizó el menú.')
print('Respaldo del código:',backup.name)
print('No se modificó storage, usuarios ni datos.')
