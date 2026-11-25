from pathlib import Path
from datetime import datetime
import shutil
from jinja2 import Environment

root=Path(__file__).resolve().parent
app=root/'app.py'
base=root/'templates/base.html'
home=root/'templates/home.html'
for f in (app,base,home):
    if not f.exists(): raise SystemExit(f'Falta {f}; no se cambió nada.')
a=app.read_text(encoding='utf-8'); b=base.read_text(encoding='utf-8'); h=home.read_text(encoding='utf-8')
if 'MEDIGRAY_EMPRESA_SELECTOR_V1' in a:
    raise SystemExit('El selector ya parece instalado. No se aplicó dos veces.')
required=['def view(req, tpl, **data):','def home(req:Request,q:str=\'\',country:int=0):',"@app.get('/artes/nuevo')", 'def company_ids(u):','def csrf(req, value):']
if any(s not in a for s in required): raise SystemExit('app.py no coincide con la versión compatible. No se modificó ningún archivo.')
if 'Inversiones Oridama' not in b or 'Inversiones Oridama' not in h or '{% if u %}' not in b:
    raise SystemExit('Plantillas diferentes a las esperadas. No se modificó ningún archivo.')
# Enrich template context with only companies actually assigned to user.
old='''def view(req, tpl, **data):
    u=user(req)
    if not req.session.get('csrf'): req.session['csrf']=secrets.token_urlsafe(32)
    return templates.TemplateResponse(req,tpl,{'u':u,'csrf':req.session.get('csrf',''),**data})'''
new='''def view(req, tpl, **data):
    u=user(req)
    if not req.session.get('csrf'): req.session['csrf']=secrets.token_urlsafe(32)
    empresas_disponibles = []
    empresa_actual = 0
    empresa_nombre = 'Seleccione una empresa'
    if u:
        with conn() as c:
            empresas_disponibles = [dict(r) for r in c.execute(
                'SELECT co.id,co.name FROM companies co JOIN user_companies uc ON uc.company_id=co.id WHERE uc.user_id=? ORDER BY co.name',
                (u['id'],)).fetchall()]
        permitidas = {r['id'] for r in empresas_disponibles}
        elegida = req.session.get('empresa_actual')
        if elegida not in permitidas and empresas_disponibles:
            elegida = empresas_disponibles[0]['id']
            req.session['empresa_actual'] = elegida
        if elegida in permitidas:
            empresa_actual = elegida
            empresa_nombre = next(x['name'] for x in empresas_disponibles if x['id']==elegida)
    return templates.TemplateResponse(req,tpl,{'u':u,'csrf':req.session.get('csrf',''),
        'empresas_disponibles':empresas_disponibles,'empresa_actual':empresa_actual,
        'empresa_nombre':empresa_nombre,**data})'''
if old not in a: raise SystemExit('La función view difiere de la versión compatible. Sin cambios.')
a=a.replace(old,new)
# Add POST /seleccionar-empresa and scope home summaries by company.
anchor="@app.get('/',response_class=HTMLResponse)"
route='''# MEDIGRAY_EMPRESA_SELECTOR_V1
@app.post('/seleccionar-empresa')
def seleccionar_empresa(req:Request, csrf_token:str=Form(...),company_id:int=Form(...)):
    u=require(req)
    csrf(req,csrf_token)
    company_access(u,company_id)
    req.session['empresa_actual']=company_id
    return go('/')

'''
a=a.replace(anchor,route+anchor,1)
start=a.index("@app.get('/',response_class=HTMLResponse)")
end=a.index("@app.get('/artes/nuevo')",start)
old_home=a[start:end]
if 'def home(req:Request,q:str=\'\',country:int=0):' not in old_home or "return view(req,'home.html'" not in old_home: raise SystemExit('Home incompatible. Sin cambios.')
new_home='''@app.get('/',response_class=HTMLResponse)
def home(req:Request,q:str='',country:int=0):
    u=require(req)
    permitidas=company_ids(u)
    actual=req.session.get('empresa_actual')
    if actual not in permitidas:
        actual=permitidas[0] if permitidas else 0
        req.session['empresa_actual']=actual
    with conn() as c:
        arts=c.execute(\'''SELECT a.*,co.name company,(SELECT COUNT(*) FROM versions v WHERE v.art_id=a.id) nversions
            FROM arts a JOIN companies co ON a.company_id=co.id
            WHERE a.company_id=? AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
            ORDER BY a.id DESC LIMIT 200\''',(actual,f'%{q}%',f'%{q}%',f'%{q}%')).fetchall()
        countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
        recent=c.execute(\'''SELECT ch.*,a.master_code FROM changes ch JOIN arts a ON a.id=ch.art_id
            WHERE a.company_id=? ORDER BY ch.id DESC LIMIT 12\''',(actual,)).fetchall()
        vlist=c.execute(\'''SELECT cv.*,v.art_id,v.revision,a.master_code,c.name country
            FROM country_versions cv JOIN versions v ON v.id=cv.version_id
            JOIN arts a ON a.id=v.art_id JOIN countries c ON c.id=cv.country_id
            WHERE a.company_id=? AND (?=0 OR cv.country_id=?) ORDER BY cv.id DESC LIMIT 100\''',
            (actual,country,country)).fetchall()
    return view(req,'home.html',arts=arts,countries=countries,recent=recent,vlist=vlist,q=q,country=country)
'''
a=a[:start]+new_home+a[end:]
# UI selector in sidebar, matching existing corporate design.
b=b.replace('<div class="brand-subtitle">Inversiones Oridama</div>', '<div class="brand-subtitle">Control de artes · Dos empresas</div>')
b=b.replace('Control de cambios de artes<br>Inversiones Oridama','Control de cambios de artes<br>{{ empresa_nombre }}')
selector='''<div class="company-switcher">
 <form method="post" action="/seleccionar-empresa">
  <input type="hidden" name="csrf_token" value="{{ csrf }}">
  <label for="company-context">Empresa de trabajo</label>
  <select id="company-context" name="company_id" onchange="this.form.submit()" aria-label="Seleccionar empresa">
   {% for co in empresas_disponibles %}
   <option value="{{ co.id }}" {% if co.id == empresa_actual %}selected{% endif %}>{{ co.name }}</option>
   {% endfor %}
  </select>
 </form>
</div>
'''
b=b.replace('<div class="nav-label">Acceso rápido</div>',selector+'<div class="nav-label">Acceso rápido</div>',1)
b=b.replace('<div class="top-sub">Consulta · Vigencias · Cambios</div>','<div class="top-sub">{{ empresa_nombre }} · Consulta · Vigencias · Cambios</div>')
css='''.company-switcher{background:#F1F7FC;border:1px solid #DCEAF6;border-radius:13px;padding:12px 12px 3px;margin:0 3px 14px}.company-switcher label{color:#23628D;font-size:11px;font-weight:800;margin-bottom:7px}.company-switcher select{font-size:12px;font-weight:700;margin-bottom:8px;min-width:0;width:100%;background:#fff}
'''
b=b.replace('</style>',css+'</style>',1)
h=h.replace('Medigray · Inversiones Oridama','{{ empresa_nombre }} · Control de artes')
# ensure templates compile before touching originals
for name, content in [('base.html',b),('home.html',h)]:
    Environment().parse(content)
compile(a, str(app),'exec')
backup=root / ('respaldo_selector_empresas_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(exist_ok=False)
for f in (app,base,home): shutil.copy2(f, backup/f.name)
app.write_text(a, encoding='utf-8');base.write_text(b,encoding='utf-8');home.write_text(h,encoding='utf-8')
print('SELECTOR DE EMPRESAS INSTALADO')
print('Respaldos:',backup)
print('Se modificaron: app.py, templates/base.html y templates/home.html')
print('No se modificaron storage, contraseñas ni datos.')
print('NOTA: Inicio filtra por empresa. Las otras rutas aún deben auditarse para aplicar el contexto a todo el sistema.')
