"""Control de artes de empaque - MVP local (Derefarma y Oridama)."""
import os, sys, sqlite3, hashlib, hmac, secrets, json, uuid, shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ARTES_DATA_DIR', str(ROOT / 'storage'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
FILES = DATA / 'files'
FILES.mkdir(exist_ok=True)
DB = DATA / 'artes.sqlite3'
SECRET_FILE = DATA / '.session-secret'
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_urlsafe(64), encoding='utf-8')
    try: os.chmod(SECRET_FILE, 0o600)
    except OSError: pass
app = FastAPI(title='Control de Artes — Derefarma / Oridama', docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_FILE.read_text().strip(), same_site='lax', https_only=os.getenv('HTTPS_ONLY') == '1', max_age=60*60*8)
templates = Jinja2Templates(directory=str(ROOT / 'templates'))
ROLES = ('Administrador', 'Regulatorios', 'Gestión de Calidad', 'Compras', 'Vista')
STATUS = ('Borrador','Pendiente interna','Pendiente sanitaria','Aprobada','Rechazada','Cerrada')
VSTATUS = ('Pendiente','Aprobado sin vigencia','Vigente Oficial','Vigente Transitorio','Inactivo/Obsoleto')

def utc(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
@contextmanager
def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally: c.close()

def init_db():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS countries(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS arts(id INTEGER PRIMARY KEY,master_code TEXT NOT NULL UNIQUE,sap_code TEXT NOT NULL,company_id INTEGER NOT NULL REFERENCES companies(id),product TEXT NOT NULL,packaging_type TEXT NOT NULL,description TEXT DEFAULT '',created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS arts_sap_idx ON arts(sap_code);
        CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY,art_id INTEGER NOT NULL REFERENCES arts(id),title TEXT NOT NULL,reason TEXT NOT NULL,approval_type TEXT NOT NULL CHECK(approval_type IN ('Interna','Sanitaria')),na_justification TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'Borrador',created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,art_id INTEGER NOT NULL REFERENCES arts(id),change_id INTEGER REFERENCES changes(id),revision TEXT NOT NULL,filename TEXT NOT NULL,stored_name TEXT NOT NULL,sha256 TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(art_id,revision));
        CREATE TABLE IF NOT EXISTS country_versions(id INTEGER PRIMARY KEY,version_id INTEGER NOT NULL REFERENCES versions(id),country_id INTEGER NOT NULL REFERENCES countries(id),local_version TEXT NOT NULL,status TEXT NOT NULL,approval_date TEXT,valid_from TEXT,transition_until TEXT,notes TEXT DEFAULT '',UNIQUE(version_id,country_id));
        CREATE INDEX IF NOT EXISTS cv_country_idx ON country_versions(country_id,status);
        CREATE TABLE IF NOT EXISTS change_diffs(id INTEGER PRIMARY KEY,change_id INTEGER NOT NULL REFERENCES changes(id),field_name TEXT NOT NULL,old_value TEXT,new_value TEXT,comment TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY,change_id INTEGER NOT NULL REFERENCES changes(id),decision TEXT NOT NULL,comment TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY,country_version_id INTEGER NOT NULL REFERENCES country_versions(id),supplier_name TEXT NOT NULL,confirmed INTEGER NOT NULL,comment TEXT,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id INTEGER,old_value TEXT,new_value TEXT,created_at TEXT NOT NULL);
        ''')
        for x in ['Derefarma','Oridama']: c.execute('INSERT OR IGNORE INTO companies(name) VALUES(?)',(x,))
        for x in ['Costa Rica','Guatemala','Honduras','El Salvador','Nicaragua','Panamá','República Dominicana']:
            c.execute('INSERT OR IGNORE INTO countries(name) VALUES(?)',(x,))
init_db()

def hash_pw(pw):
    salt = secrets.token_bytes(16)
    return f'{salt.hex()}:{hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310000).hex()}'
def check_pw(pw, stored):
    try:
        salt, digest=stored.split(':',1)
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', pw.encode(),bytes.fromhex(salt),310000),bytes.fromhex(digest))
    except (ValueError, TypeError): return False

def audit(c, uid, action, entity, eid=None, old=None, new=None):
    c.execute('INSERT INTO audit(actor_id,action,entity,entity_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?)',
              (uid,action,entity,eid,json.dumps(old,ensure_ascii=False) if old is not None else None,json.dumps(new,ensure_ascii=False) if new is not None else None,utc()))

def user(req):
    uid=req.session.get('uid')
    if not uid: return None
    with conn() as c:
        r=c.execute('SELECT id,email,role FROM users WHERE id=? AND active=1',(uid,)).fetchone()
        return dict(r) if r else None

def require(req, *roles):
    u=user(req)
    if not u: raise HTTPException(401, 'Inicie sesión')
    if roles and u['role'] not in roles: raise HTTPException(403, 'No tiene permiso para esta acción')
    return u

def csrf(req, value):
    if not value or not hmac.compare_digest(str(value),str(req.session.get('csrf',''))):
        raise HTTPException(403,'Token de formulario inválido')
def view(req, tpl, **data):
    u=user(req)
    if not req.session.get('csrf'): req.session['csrf']=secrets.token_urlsafe(32)
    return templates.TemplateResponse(req,tpl,{'u':u,'csrf':req.session.get('csrf',''),**data})
def go(path): return RedirectResponse(path,status_code=303)
def val_int(s):
    try: return int(s)
    except (TypeError,ValueError): raise HTTPException(400,'Identificador inválido')
def ensure(c, table, id):
    if table not in ('arts','changes','versions','country_versions','users'): raise ValueError('Tabla inválida')
    r=c.execute(f'SELECT * FROM {table} WHERE id=?',(id,)).fetchone()
    if not r: raise HTTPException(404,'Registro no encontrado')
    return r

def date_ok(d):
    if not d: return None
    try: return datetime.strptime(d,'%Y-%m-%d').date().isoformat()
    except ValueError: raise HTTPException(400,'Fecha inválida')

@app.get('/login',response_class=HTMLResponse)
def login_form(req:Request):
    if user(req): return go('/')
    return view(req,'login.html',error='')
@app.post('/login')
def login(req:Request,email:str=Form(...),password:str=Form(...),csrf_token:str=Form(...)):
    csrf(req,csrf_token)
    with conn() as c:
        r=c.execute('SELECT * FROM users WHERE lower(email)=lower(?) AND active=1',(email.strip(),)).fetchone()
    if not r or not check_pw(password,r['password']): return view(req,'login.html',error='Correo o contraseña incorrectos')
    req.session.clear()
    req.session['uid']=r['id'];req.session['csrf']=secrets.token_urlsafe(32)
    return go('/')
@app.post('/logout')
def logout(req:Request,csrf_token:str=Form(...)):
    csrf(req,csrf_token);req.session.clear();return go('/login')
@app.get('/',response_class=HTMLResponse)
def home(req:Request,q:str='',country:int=0):
    require(req)
    with conn() as c:
        arts=c.execute('''SELECT a.*,co.name company,(SELECT COUNT(*) FROM versions v WHERE v.art_id=a.id) nversions FROM arts a JOIN companies co ON a.company_id=co.id WHERE a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ? ORDER BY a.id DESC LIMIT 200''',(f'%{q}%',)*3).fetchall()
        countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
        recent=c.execute('''SELECT ch.*,a.master_code FROM changes ch JOIN arts a ON a.id=ch.art_id ORDER BY ch.id DESC LIMIT 12''').fetchall()
        vlist=c.execute('''SELECT cv.*,v.art_id,v.revision,a.master_code,c.name country FROM country_versions cv JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id JOIN countries c ON c.id=cv.country_id WHERE (?=0 OR cv.country_id=?) ORDER BY cv.id DESC LIMIT 100''',(country,country)).fetchall()
    return view(req,'home.html',arts=arts,countries=countries,recent=recent,vlist=vlist,q=q,country=country)
@app.get('/artes/nuevo')
def art_new(req:Request):
    require(req,'Administrador','Regulatorios')
    with conn() as c: cos=c.execute('SELECT * FROM companies').fetchall()
    return view(req,'art_form.html',companies=cos)
@app.post('/artes/nuevo')
def art_create(req:Request,csrf_token:str=Form(...),master_code:str=Form(...),sap_code:str=Form(...),company_id:int=Form(...),product:str=Form(...),packaging_type:str=Form(...),description:str=Form('')):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    if not all(x.strip() for x in [master_code,sap_code,product,packaging_type]): raise HTTPException(400,'Complete los campos obligatorios')
    with conn() as c:
        try:
            cur=c.execute('INSERT INTO arts(master_code,sap_code,company_id,product,packaging_type,description,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(master_code.strip(),sap_code.strip(),company_id,product.strip(),packaging_type.strip(),description.strip(),u['id'],utc()))
        except sqlite3.IntegrityError: raise HTTPException(400,'Código maestro duplicado o empresa inexistente')
        audit(c,u['id'],'CREAR','Arte',cur.lastrowid,new={'master_code':master_code,'sap_code':sap_code})
        id=cur.lastrowid
    return go(f'/artes/{id}')
@app.get('/artes/{art_id}')
def art_detail(req:Request,art_id:int):
    require(req)
    with conn() as c:
        art=c.execute('SELECT a.*,co.name company FROM arts a JOIN companies co ON co.id=a.company_id WHERE a.id=?',(art_id,)).fetchone()
        if not art: raise HTTPException(404,'Arte inexistente')
        vers=c.execute('''SELECT v.*,u.email creator FROM versions v JOIN users u ON u.id=v.created_by WHERE v.art_id=? ORDER BY v.id DESC''',(art_id,)).fetchall()
        cvers=c.execute('''SELECT cv.*,p.name country,v.revision,v.id version_id FROM country_versions cv JOIN countries p ON p.id=cv.country_id JOIN versions v ON v.id=cv.version_id WHERE v.art_id=? ORDER BY p.name,cv.id DESC''',(art_id,)).fetchall()
        changes=c.execute('SELECT * FROM changes WHERE art_id=? ORDER BY id DESC',(art_id,)).fetchall()
        countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
        suppliers=c.execute('''SELECT s.*,u.email FROM suppliers s JOIN users u ON u.id=s.actor_id WHERE s.country_version_id IN (SELECT cv.id FROM country_versions cv JOIN versions v ON v.id=cv.version_id WHERE v.art_id=?) ORDER BY s.id DESC LIMIT 30''',(art_id,)).fetchall()
    return view(req,'art_detail.html',art=art,vers=vers,cvers=cvers,changes=changes,countries=countries,suppliers=suppliers,status=VSTATUS)
@app.post('/artes/{art_id}/version')
async def add_version(req:Request,art_id:int,csrf_token:str=Form(...),revision:str=Form(...),change_id:str=Form(''),pdf:UploadFile=File(...)):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    if not revision.strip() or not pdf.filename.lower().endswith('.pdf'): raise HTTPException(400,'Se requiere una revisión y archivo PDF')
    temp=FILES / f'temp_{uuid.uuid4().hex}'
    total=0; sha=hashlib.sha256()
    try:
        with temp.open('wb') as f:
            header=await pdf.read(5)
            if header != b'%PDF-': raise HTTPException(400,'El archivo no tiene cabecera PDF válida')
            f.write(header);sha.update(header);total+=len(header)
            while True:
                block=await pdf.read(1024*1024)
                if not block: break
                total+=len(block)
                if total>20*1024*1024: raise HTTPException(413,'Máximo 20 MB')
                f.write(block);sha.update(block)
        stored=f'{uuid.uuid4().hex}.pdf'
        with conn() as c:
            ensure(c,'arts',art_id)
            chid=val_int(change_id) if change_id else None
            if chid:
                ch=ensure(c,'changes',chid)
                if ch['art_id']!=art_id: raise HTTPException(400,'El control de cambio pertenece a otro arte')
            try:
                cur=c.execute('INSERT INTO versions(art_id,change_id,revision,filename,stored_name,sha256,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(art_id,chid,revision.strip(),Path(pdf.filename).name[:200],stored,sha.hexdigest(),u['id'],utc()))
            except sqlite3.IntegrityError: raise HTTPException(400,'Esta revisión ya existe')
            os.replace(temp,FILES/stored)
            audit(c,u['id'],'SUBIR_PDF','Version',cur.lastrowid,new={'sha256':sha.hexdigest(),'revision':revision})
    finally:
        if temp.exists(): temp.unlink()
        await pdf.close()
    return go(f'/artes/{art_id}')
@app.get('/documentos/{version_id}')
def download(req:Request,version_id:int):
    u=require(req)
    with conn() as c:
        v=ensure(c,'versions',version_id)
        audit(c,u['id'],'DESCARGAR','Version',version_id)
    p=FILES/v['stored_name']
    if not p.exists(): raise HTTPException(404,'Archivo no encontrado')
    return FileResponse(p,media_type='application/pdf',filename=v['filename'],content_disposition_type='attachment',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
@app.post('/versiones/{version_id}/pais')
def add_country(req:Request,version_id:int,csrf_token:str=Form(...),country_id:int=Form(...),local_version:str=Form(...),status:str=Form('Pendiente'),approval_date:str=Form(''),valid_from:str=Form(''),transition_until:str=Form(''),notes:str=Form('')):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    if status not in VSTATUS: raise HTTPException(400,'Estado no permitido')
    if status in ('Vigente Oficial','Vigente Transitorio') and not valid_from: raise HTTPException(400,'Debe registrar la fecha de inicio de vigencia')
    if status=='Vigente Transitorio' and not transition_until: raise HTTPException(400,'Indique fecha fin de transición')
    with conn() as c:
        v=ensure(c,'versions',version_id)
        # Evita dos artes simultáneamente oficiales para el mismo arte/país.
        if status=='Vigente Oficial':
            existing=c.execute('''SELECT cv.id FROM country_versions cv JOIN versions x ON x.id=cv.version_id WHERE x.art_id=? AND cv.country_id=? AND cv.status='Vigente Oficial' LIMIT 1''',(v['art_id'],country_id)).fetchone()
            if existing: raise HTTPException(400,'Ya existe una versión vigente oficial para este arte y país. Cierre o cambie la anterior antes de activar la nueva.')
        try:
            cur=c.execute('INSERT INTO country_versions(version_id,country_id,local_version,status,approval_date,valid_from,transition_until,notes) VALUES(?,?,?,?,?,?,?,?)',(version_id,country_id,local_version.strip(),status,date_ok(approval_date),date_ok(valid_from),date_ok(transition_until),notes))
        except sqlite3.IntegrityError: raise HTTPException(400,'La versión ya está asociada al país o el país no existe')
        audit(c,u['id'],'ASIGNAR_PAIS','VersionPorPais',cur.lastrowid,new={'status':status,'country_id':country_id})
    return go(f'/artes/{v["art_id"]}')
@app.post('/paises-version/{cv_id}/estado')
def change_status(req:Request,cv_id:int,csrf_token:str=Form(...),status:str=Form(...),notes:str=Form(''),valid_from:str=Form(''),transition_until:str=Form('')):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    if status not in VSTATUS: raise HTTPException(400,'Estado no permitido')
    with conn() as c:
        old=ensure(c,'country_versions',cv_id)
        v=ensure(c,'versions',old['version_id'])
        new_start=date_ok(valid_from) or old['valid_from']
        new_end=date_ok(transition_until) or old['transition_until']
        if status in ('Vigente Oficial','Vigente Transitorio') and not new_start: raise HTTPException(400,'Falta fecha de vigencia')
        if status=='Vigente Transitorio' and not new_end: raise HTTPException(400,'Falta fin de transición')
        if status=='Vigente Oficial':
            exists=c.execute('''SELECT cv.id FROM country_versions cv JOIN versions v ON v.id=cv.version_id WHERE v.art_id=? AND cv.country_id=? AND cv.status='Vigente Oficial' AND cv.id != ?''',(v['art_id'],old['country_id'],cv_id)).fetchone()
            if exists: raise HTTPException(400,'Primero cambie el estado de la versión oficial anterior')
        c.execute('UPDATE country_versions SET status=?,notes=?,valid_from=?,transition_until=? WHERE id=?',(status,notes,new_start,new_end,cv_id))
        audit(c,u['id'],'CAMBIAR_ESTADO','VersionPorPais',cv_id,old={'status':old['status']},new={'status':status,'notes':notes})
    return go(f'/artes/{v["art_id"]}')
@app.post('/paises-version/{cv_id}/proveedor')
def supplier(req:Request,cv_id:int,csrf_token:str=Form(...),supplier_name:str=Form(...),confirmed:str=Form('No'),comment:str=Form('')):
    u=require(req,'Administrador','Regulatorios','Compras');csrf(req,csrf_token)
    if confirmed not in ('Sí','No'): raise HTTPException(400,'Valor inválido')
    with conn() as c:
        cv=ensure(c,'country_versions',cv_id);v=ensure(c,'versions',cv['version_id'])
        cur=c.execute('INSERT INTO suppliers(country_version_id,supplier_name,confirmed,comment,actor_id,created_at) VALUES(?,?,?,?,?,?)',(cv_id,supplier_name.strip(),int(confirmed=='Sí'),comment.strip(),u['id'],utc()))
        audit(c,u['id'],'CONFIRMAR_PROVEEDOR','Proveedor',cur.lastrowid,new={'confirmed':confirmed,'supplier':supplier_name})
    return go(f'/artes/{v["art_id"]}')
@app.get('/artes/{art_id}/cambios/nuevo')
def change_new(req:Request,art_id:int):
    require(req)
    with conn() as c: art=ensure(c,'arts',art_id)
    return view(req,'change_form.html',art=art)
@app.post('/artes/{art_id}/cambios/nuevo')
def change_create(req:Request,art_id:int,csrf_token:str=Form(...),title:str=Form(...),reason:str=Form(...),approval_type:str=Form(...),na_justification:str=Form('')):
    u=require(req);csrf(req,csrf_token)
    if approval_type not in ('Interna','Sanitaria'): raise HTTPException(400,'Tipo inválido')
    with conn() as c:
        ensure(c,'arts',art_id)
        cur=c.execute('INSERT INTO changes(art_id,title,reason,approval_type,na_justification,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(art_id,title.strip(),reason.strip(),approval_type,na_justification.strip(),u['id'],utc(),utc()))
        audit(c,u['id'],'CREAR','Cambio',cur.lastrowid,new={'title':title,'approval_type':approval_type})
        id=cur.lastrowid
    return go(f'/cambios/{id}')
@app.get('/cambios/{change_id}')
def change_detail(req:Request,change_id:int):
    require(req)
    with conn() as c:
        ch=c.execute('SELECT x.*,a.master_code,u.email creator FROM changes x JOIN arts a ON a.id=x.art_id JOIN users u ON u.id=x.created_by WHERE x.id=?',(change_id,)).fetchone()
        if not ch: raise HTTPException(404,'Control de cambios no encontrado')
        diffs=c.execute('SELECT * FROM change_diffs WHERE change_id=? ORDER BY id DESC',(change_id,)).fetchall()
        approvals=c.execute('SELECT ap.*,u.email FROM approvals ap JOIN users u ON u.id=ap.actor_id WHERE ap.change_id=? ORDER BY ap.id DESC',(change_id,)).fetchall()
    return view(req,'change_detail.html',ch=ch,diffs=diffs,approvals=approvals)
@app.post('/cambios/{change_id}/afectacion')
def add_diff(req:Request,change_id:int,csrf_token:str=Form(...),field_name:str=Form(...),old_value:str=Form(''),new_value:str=Form(''),comment:str=Form('')):
    u=require(req,'Administrador','Regulatorios','Gestión de Calidad');csrf(req,csrf_token)
    with conn() as c:
        ch=ensure(c,'changes',change_id)
        if ch['status'] in ('Aprobada','Rechazada','Cerrada'): raise HTTPException(400,'El cambio ya está cerrado a edición')
        cur=c.execute('INSERT INTO change_diffs(change_id,field_name,old_value,new_value,comment,created_at) VALUES(?,?,?,?,?,?)',(change_id,field_name,old_value,new_value,comment,utc()))
        audit(c,u['id'],'AÑADIR_AFECTACION','Cambio',change_id,new={'diff_id':cur.lastrowid,'field':field_name})
    return go(f'/cambios/{change_id}')
@app.post('/cambios/{change_id}/enviar')
def submit_change(req:Request,change_id:int,csrf_token:str=Form(...)):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    with conn() as c:
        ch=ensure(c,'changes',change_id)
        if ch['status']!='Borrador': raise HTTPException(400,'Solo puede enviar borradores')
        status='Pendiente sanitaria' if ch['approval_type']=='Sanitaria' else 'Pendiente interna'
        c.execute('UPDATE changes SET status=?,updated_at=? WHERE id=?',(status,utc(),change_id))
        audit(c,u['id'],'ENVIAR_APROBACION','Cambio',change_id,old={'status':'Borrador'},new={'status':status})
    return go(f'/cambios/{change_id}')
@app.post('/cambios/{change_id}/aprobar')
def approve(req:Request,change_id:int,csrf_token:str=Form(...),decision:str=Form(...),comment:str=Form(...)):
    u=require(req,'Administrador','Regulatorios','Gestión de Calidad');csrf(req,csrf_token)
    if decision not in ('Aprobar','Rechazar') or not comment.strip(): raise HTTPException(400,'Decisión y comentario obligatorios')
    with conn() as c:
        ch=ensure(c,'changes',change_id)
        if ch['status'] not in ('Pendiente sanitaria','Pendiente interna'): raise HTTPException(400,'No está pendiente')
        if ch['created_by']==u['id']: raise HTTPException(403,'No se permite aprobar el cambio propio')
        if ch['approval_type']=='Sanitaria' and u['role'] not in ('Administrador','Regulatorios'): raise HTTPException(403,'Solo Regulatorios puede registrar esta decisión')
        status='Aprobada' if decision=='Aprobar' else 'Rechazada'
        c.execute('INSERT INTO approvals(change_id,decision,comment,actor_id,created_at) VALUES(?,?,?,?,?)',(change_id,decision,comment.strip(),u['id'],utc()))
        c.execute('UPDATE changes SET status=?,updated_at=? WHERE id=?',(status,utc(),change_id))
        audit(c,u['id'],'DECIDIR_APROBACION','Cambio',change_id,old={'status':ch['status']},new={'status':status,'comment':comment})
    return go(f'/cambios/{change_id}')
@app.get('/alertas')
def alerts(req:Request):
    require(req)
    today=datetime.now(timezone.utc).date()
    limit=(today+timedelta(days=60)).isoformat()
    with conn() as c:
        records=c.execute('''SELECT cv.*,p.name country,a.master_code,v.revision FROM country_versions cv JOIN countries p ON p.id=cv.country_id JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id WHERE cv.transition_until IS NOT NULL AND cv.transition_until<=? AND cv.status='Vigente Transitorio' ORDER BY cv.transition_until''',(limit,)).fetchall()
        pending=c.execute("SELECT ch.*,a.master_code FROM changes ch JOIN arts a ON a.id=ch.art_id WHERE ch.status LIKE 'Pendiente%' ORDER BY ch.id DESC").fetchall()
    return view(req,'alerts.html',records=records,pending=pending,today=today.isoformat())
@app.get('/auditoria')
def audits(req:Request):
    require(req,'Administrador','Regulatorios','Gestión de Calidad')
    with conn() as c:
        rows=c.execute('SELECT au.*,u.email FROM audit au LEFT JOIN users u ON u.id=au.actor_id ORDER BY au.id DESC LIMIT 250').fetchall()
    return view(req,'audit.html',rows=rows)
@app.get('/usuarios')
def users(req:Request):
    require(req,'Administrador')
    with conn() as c: rows=c.execute('SELECT id,email,role,active,created_at FROM users ORDER BY id').fetchall()
    return view(req,'users.html',rows=rows,roles=ROLES)
@app.post('/usuarios')
def create_user(req:Request,csrf_token:str=Form(...),email:str=Form(...),password:str=Form(...),role:str=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token)
    if role not in ROLES or len(password)<12: raise HTTPException(400,'Rol inválido o contraseña menor de 12 caracteres')
    with conn() as c:
        try: cur=c.execute('INSERT INTO users(email,password,role,created_at) VALUES(?,?,?,?)',(email.lower().strip(),hash_pw(password),role,utc()))
        except sqlite3.IntegrityError: raise HTTPException(400,'Correo ya registrado')
        audit(c,u['id'],'CREAR_USUARIO','Usuario',cur.lastrowid,new={'email':email,'role':role})
    return go('/usuarios')
@app.post('/usuarios/{uid}/estado')
def user_state(req:Request,uid:int,csrf_token:str=Form(...),active:int=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token)
    if uid==u['id'] or active not in (0,1): raise HTTPException(400,'Operación no permitida')
    with conn() as c:
        ensure(c,'users',uid)
        c.execute('UPDATE users SET active=? WHERE id=?',(active,uid))
        audit(c,u['id'],'CAMBIAR_ACCESO','Usuario',uid,new={'active':active})
    return go('/usuarios')

def bootstrap():
    print('=== Creación del primer administrador ===')
    from getpass import getpass
    email=input('Correo administrador: ').strip().lower()
    pw=getpass('Contraseña (mínimo 12 caracteres): ')
    if len(pw)<12 or '@' not in email: sys.exit('Correo o contraseña inválidos')
    with conn() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]: sys.exit('Ya existen usuarios. Use la pantalla Usuarios.')
        cur=c.execute('INSERT INTO users(email,password,role,created_at) VALUES(?,?,?,?)',(email,hash_pw(pw),'Administrador',utc()))
        audit(c,cur.lastrowid,'BOOTSTRAP','Usuario',cur.lastrowid)
    print('Administrador creado. Ejecute: uvicorn app:app --reload')
if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='crear-admin': bootstrap()
    else: print('Use: python app.py crear-admin')
