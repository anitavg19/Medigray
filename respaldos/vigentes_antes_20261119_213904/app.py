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
app = FastAPI(title='Medigray — DR Pharma / Inversiones Oridama', docs_url=None, redoc_url=None)
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
        CREATE TABLE IF NOT EXISTS arts(id INTEGER PRIMARY KEY,master_code TEXT NOT NULL,sap_code TEXT NOT NULL,company_id INTEGER NOT NULL REFERENCES companies(id),product TEXT NOT NULL,packaging_type TEXT NOT NULL,description TEXT DEFAULT '',created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS arts_sap_idx ON arts(sap_code);
        CREATE UNIQUE INDEX IF NOT EXISTS arts_company_master_idx ON arts(company_id,master_code);
        CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY,art_id INTEGER NOT NULL REFERENCES arts(id),title TEXT NOT NULL,reason TEXT NOT NULL,approval_type TEXT NOT NULL CHECK(approval_type IN ('Interna','Sanitaria')),na_justification TEXT DEFAULT '',status TEXT NOT NULL DEFAULT 'Borrador',created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,art_id INTEGER NOT NULL REFERENCES arts(id),change_id INTEGER REFERENCES changes(id),revision TEXT NOT NULL,filename TEXT NOT NULL,stored_name TEXT NOT NULL,sha256 TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(art_id,revision));
        CREATE TABLE IF NOT EXISTS country_versions(id INTEGER PRIMARY KEY,version_id INTEGER NOT NULL REFERENCES versions(id),country_id INTEGER NOT NULL REFERENCES countries(id),local_version TEXT NOT NULL,status TEXT NOT NULL,approval_date TEXT,valid_from TEXT,transition_until TEXT,notes TEXT DEFAULT '',UNIQUE(version_id,country_id));
        CREATE INDEX IF NOT EXISTS cv_country_idx ON country_versions(country_id,status);
        CREATE TABLE IF NOT EXISTS change_diffs(id INTEGER PRIMARY KEY,change_id INTEGER NOT NULL REFERENCES changes(id),field_name TEXT NOT NULL,old_value TEXT,new_value TEXT,comment TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY,change_id INTEGER NOT NULL REFERENCES changes(id),decision TEXT NOT NULL,comment TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY,country_version_id INTEGER NOT NULL REFERENCES country_versions(id),supplier_name TEXT NOT NULL,confirmed INTEGER NOT NULL,comment TEXT,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id INTEGER,old_value TEXT,new_value TEXT,created_at TEXT NOT NULL);
        ''')
        # Nombres oficiales; conserva los IDs y los artes de una instalación previa.
        c.execute("UPDATE companies SET name='DR Pharma' WHERE name='Derefarma'")
        c.execute("UPDATE companies SET name='Inversiones Oridama' WHERE name='Oridama'")
        for x in ['DR Pharma','Inversiones Oridama']: c.execute('INSERT OR IGNORE INTO companies(name) VALUES(?)',(x,))
        c.executescript('''
        CREATE TABLE IF NOT EXISTS user_companies(user_id INTEGER NOT NULL REFERENCES users(id),company_id INTEGER NOT NULL REFERENCES companies(id),PRIMARY KEY(user_id,company_id));
        CREATE TABLE IF NOT EXISTS form_fields(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),form_key TEXT NOT NULL DEFAULT 'solicitud',label TEXT NOT NULL,field_type TEXT NOT NULL,choices_json TEXT DEFAULT '[]',required INTEGER DEFAULT 0,active INTEGER DEFAULT 1,sort_order INTEGER DEFAULT 100,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS form_answers(id INTEGER PRIMARY KEY,change_id INTEGER NOT NULL REFERENCES changes(id),field_id INTEGER NOT NULL REFERENCES form_fields(id),answer TEXT NOT NULL DEFAULT '',UNIQUE(change_id,field_id));
        CREATE TABLE IF NOT EXISTS company_modules(company_id INTEGER NOT NULL REFERENCES companies(id),module_key TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(company_id,module_key));
        CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),art_id INTEGER REFERENCES arts(id),change_id INTEGER REFERENCES changes(id),filename TEXT NOT NULL,content_type TEXT NOT NULL,storage_type TEXT NOT NULL,storage_key TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS storage_settings(company_id INTEGER PRIMARY KEY REFERENCES companies(id),destination TEXT NOT NULL DEFAULT 'local',drive_folder_id TEXT DEFAULT '');
        ''')
        # Para bases antiguas, se restringe acceso a empresas antes de crear usuarios nuevos.
        for company in c.execute('SELECT id FROM companies').fetchall():
            cid=company['id']
            for module in ['artes','solicitudes','versiones','proveedores','alertas','auditoria','adjuntos']:
                c.execute('INSERT OR IGNORE INTO company_modules(company_id,module_key,enabled) VALUES(?,?,1)',(cid,module))
            c.execute('INSERT OR IGNORE INTO storage_settings(company_id) VALUES(?)',(cid,))
        # Solo los administradores existentes reciben acceso inicial a ambas empresas.
        c.execute("INSERT OR IGNORE INTO user_companies(user_id,company_id) SELECT u.id, co.id FROM users u CROSS JOIN companies co WHERE u.role='Administrador'")
        for x in ['Costa Rica','Guatemala','Honduras','El Salvador','Nicaragua','Panamá','República Dominicana']:
            c.execute('INSERT OR IGNORE INTO countries(name) VALUES(?)',(x,))
init_db()
# Documento externo por arte; no guarda credenciales ni copia automática de Drive.
with conn() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS art_document_links (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), art_id INTEGER NOT NULL REFERENCES arts(id), url TEXT NOT NULL, description TEXT, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)')


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

def company_ids(u):
    with conn() as c:
        return [r['company_id'] for r in c.execute('SELECT company_id FROM user_companies WHERE user_id=?',(u['id'],)).fetchall()]

def company_access(u,cid):
    if int(cid) not in company_ids(u): raise HTTPException(403,'Sin acceso a la empresa de este registro')

def art_access(u,art_id):
    with conn() as c:
        a=ensure(c,'arts',art_id)
        company_access(u,a['company_id'])
        return a['company_id']

def change_access(u,change_id):
    with conn() as c:
        row=ensure(c,'changes',change_id)
        return art_access(u,row['art_id'])

def version_access(u,version_id):
    with conn() as c:
        row=ensure(c,'versions',version_id)
        return art_access(u,row['art_id'])

def cv_access(u,cv_id):
    with conn() as c:
        row=ensure(c,'country_versions',cv_id)
        return version_access(u,row['version_id'])

def module_access(u,cid,key):
    company_access(u,cid)
    with conn() as c:
        row=c.execute('SELECT enabled FROM company_modules WHERE company_id=? AND module_key=?',(cid,key)).fetchone()
        if row and not row['enabled']: raise HTTPException(403,'Módulo desactivado para esta empresa')

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
        'empresa_nombre':empresa_nombre,**data})
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
# MEDIGRAY_EMPRESA_SELECTOR_V1
@app.post('/seleccionar-empresa')
def seleccionar_empresa(req:Request, csrf_token:str=Form(...),company_id:int=Form(...)):
    u=require(req)
    csrf(req,csrf_token)
    company_access(u,company_id)
    req.session['empresa_actual']=company_id
    return go('/')

@app.get('/',response_class=HTMLResponse)
def home(req:Request,q:str='',country:int=0):
    u=require(req)
    permitidas=company_ids(u)
    actual=req.session.get('empresa_actual')
    if actual not in permitidas:
        actual=permitidas[0] if permitidas else 0
        req.session['empresa_actual']=actual
    with conn() as c:
        arts=c.execute('''SELECT a.*,co.name company,(SELECT COUNT(*) FROM versions v WHERE v.art_id=a.id) nversions
            FROM arts a JOIN companies co ON a.company_id=co.id
            WHERE a.company_id=? AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
            ORDER BY a.id DESC LIMIT 200''',(actual,f'%{q}%',f'%{q}%',f'%{q}%')).fetchall()
        countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
        recent=c.execute('''SELECT ch.*,a.master_code FROM changes ch JOIN arts a ON a.id=ch.art_id
            WHERE a.company_id=? ORDER BY ch.id DESC LIMIT 12''',(actual,)).fetchall()
        vlist=c.execute('''SELECT cv.*,v.art_id,v.revision,a.master_code,c.name country
            FROM country_versions cv JOIN versions v ON v.id=cv.version_id
            JOIN arts a ON a.id=v.art_id JOIN countries c ON c.id=cv.country_id
            WHERE a.company_id=? AND (?=0 OR cv.country_id=?) ORDER BY cv.id DESC LIMIT 100''',
            (actual,country,country)).fetchall()
    return view(req,'home.html',arts=arts,countries=countries,recent=recent,vlist=vlist,q=q,country=country)
@app.get('/artes/nuevo')
def art_new(req:Request):
    require(req,'Administrador','Regulatorios')
    with conn() as c: cos=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?)',(require(req)['id'],)).fetchall()
    return view(req,'art_form.html',companies=cos)
# MEDIGRAY_DOC_ARTE_V1: documento obligatorio al crear el arte maestro.
@app.post('/artes/nuevo')
async def art_create(req:Request,csrf_token:str=Form(...),master_code:str=Form(...),sap_code:str=Form(...),company_id:int=Form(...),product:str=Form(...),packaging_type:str=Form(...),description:str=Form(''),document_mode:str=Form('archivo'),drive_url:str=Form(''),document_file:UploadFile=File(None)):
    from urllib.parse import urlparse
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    company_access(u,company_id);module_access(u,company_id,'artes')
    if not all(x.strip() for x in [master_code,sap_code,product,packaging_type]):
        raise HTTPException(400,'Complete los campos obligatorios')
    if document_mode not in ('archivo','drive'):
        raise HTTPException(400,'Seleccione archivo o enlace Drive')
    payload=None;filename=None;ext=None;digest=None;drive_url=drive_url.strip()
    if document_mode=='archivo':
        if document_file is None or not document_file.filename:
            raise HTTPException(400,'Todo arte debe tener un documento: adjunte un PDF o Word')
        filename=Path(document_file.filename).name[:180]
        ext=Path(filename).suffix.lower()
        if ext not in ('.pdf','.docx'):
            raise HTTPException(400,'Para el documento inicial se admite PDF o DOCX')
        payload=await document_file.read(20*1024*1024+1)
        await document_file.close()
        if not payload or len(payload)>20*1024*1024:
            raise HTTPException(413,'Documento vacío o superior a 20 MB')
        if ext=='.pdf' and not payload.startswith(b'%PDF-'):
            raise HTTPException(400,'PDF inválido')
        if ext=='.docx' and not payload.startswith(b'PK'):
            raise HTTPException(400,'Archivo DOCX inválido')
        digest=hashlib.sha256(payload).hexdigest()
    else:
        parsed=urlparse(drive_url)
        if parsed.scheme!='https' or parsed.hostname not in ('drive.google.com','docs.google.com'):
            raise HTTPException(400,'Introduzca un enlace HTTPS válido de Google Drive o Google Docs')
        if not parsed.path or parsed.path=='/':
            raise HTTPException(400,'Introduzca el enlace directo a un documento de Drive')
    with conn() as c:
        try:
            cur=c.execute('INSERT INTO arts(master_code,sap_code,company_id,product,packaging_type,description,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(master_code.strip(),sap_code.strip(),company_id,product.strip(),packaging_type.strip(),description.strip(),u['id'],utc()))
        except sqlite3.IntegrityError:
            raise HTTPException(400,'Código maestro duplicado o empresa inexistente')
        aid=cur.lastrowid
        if document_mode=='archivo':
            stored=uuid.uuid4().hex+ext
            (FILES/stored).write_bytes(payload)
            media='application/pdf' if ext=='.pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            c.execute('INSERT INTO attachments(company_id,art_id,filename,content_type,storage_type,storage_key,sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(company_id,aid,filename,media,'local',stored,digest,len(payload),u['id'],utc()))
        else:
            c.execute('INSERT INTO art_document_links(company_id,art_id,url,description,created_by,created_at) VALUES(?,?,?,?,?,?)',(company_id,aid,drive_url,'Documento inicial en Google Drive',u['id'],utc()))
        audit(c,u['id'],'CREAR','Arte',aid,new={'master_code':master_code,'sap_code':sap_code,'document_mode':document_mode})
    return go(f'/artes/{aid}')
@app.get('/artes/{art_id}')
def art_detail(req:Request,art_id:int):
    require(req)
    cid=art_access(require(req),art_id)
    module_access(require(req),cid,'artes')
    with conn() as c:
        art=c.execute('SELECT a.*,co.name company FROM arts a JOIN companies co ON co.id=a.company_id WHERE a.id=?',(art_id,)).fetchone()
        if not art: raise HTTPException(404,'Arte inexistente')
        vers=c.execute('''SELECT v.*,u.email creator FROM versions v JOIN users u ON u.id=v.created_by WHERE v.art_id=? ORDER BY v.id DESC''',(art_id,)).fetchall()
        cvers=c.execute('''SELECT cv.*,p.name country,v.revision,v.id version_id FROM country_versions cv JOIN countries p ON p.id=cv.country_id JOIN versions v ON v.id=cv.version_id WHERE v.art_id=? ORDER BY p.name,cv.id DESC''',(art_id,)).fetchall()
        changes=c.execute('SELECT * FROM changes WHERE art_id=? ORDER BY id DESC',(art_id,)).fetchall()
        countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
        suppliers=c.execute('''SELECT s.*,u.email FROM suppliers s JOIN users u ON u.id=s.actor_id WHERE s.country_version_id IN (SELECT cv.id FROM country_versions cv JOIN versions v ON v.id=cv.version_id WHERE v.art_id=?) ORDER BY s.id DESC LIMIT 30''',(art_id,)).fetchall()
        attachments=c.execute('SELECT * FROM attachments WHERE art_id=? ORDER BY id DESC',(art_id,)).fetchall()
        document_links=c.execute('SELECT * FROM art_document_links WHERE art_id=? AND company_id=? ORDER BY id DESC',(art_id,cid)).fetchall()
    return view(req,'art_detail.html',art=art,vers=vers,cvers=cvers,changes=changes,countries=countries,suppliers=suppliers,status=VSTATUS,attachments=attachments,document_links=document_links)
@app.post('/artes/{art_id}/version')
async def add_version(req:Request,art_id:int,csrf_token:str=Form(...),revision:str=Form(...),change_id:str=Form(''),pdf:UploadFile=File(...)):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    cid=art_access(u,art_id)
    module_access(u,cid,'versiones')
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
    cid=version_access(u,version_id)
    module_access(u,cid,'versiones')
    with conn() as c:
        v=ensure(c,'versions',version_id)
        audit(c,u['id'],'DESCARGAR','Version',version_id)
    p=FILES/v['stored_name']
    if not p.exists(): raise HTTPException(404,'Archivo no encontrado')
    return FileResponse(p,media_type='application/pdf',filename=v['filename'],content_disposition_type='attachment',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
@app.post('/versiones/{version_id}/pais')
def add_country(req:Request,version_id:int,csrf_token:str=Form(...),country_id:int=Form(...),local_version:str=Form(...),status:str=Form('Pendiente'),approval_date:str=Form(''),valid_from:str=Form(''),transition_until:str=Form(''),notes:str=Form('')):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    cid=version_access(u,version_id)
    module_access(u,cid,'versiones')
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
    cid=cv_access(u,cv_id)
    module_access(u,cid,'versiones')
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
    cid=cv_access(u,cv_id)
    module_access(u,cid,'proveedores')
    if confirmed not in ('Sí','No'): raise HTTPException(400,'Valor inválido')
    with conn() as c:
        cv=ensure(c,'country_versions',cv_id);v=ensure(c,'versions',cv['version_id'])
        cur=c.execute('INSERT INTO suppliers(country_version_id,supplier_name,confirmed,comment,actor_id,created_at) VALUES(?,?,?,?,?,?)',(cv_id,supplier_name.strip(),int(confirmed=='Sí'),comment.strip(),u['id'],utc()))
        audit(c,u['id'],'CONFIRMAR_PROVEEDOR','Proveedor',cur.lastrowid,new={'confirmed':confirmed,'supplier':supplier_name})
    return go(f'/artes/{v["art_id"]}')
@app.get('/artes/{art_id}/cambios/nuevo')
def change_new(req:Request,art_id:int):
    require(req)
    cid=art_access(require(req),art_id)
    module_access(require(req),cid,'solicitudes')
    with conn() as c: art=ensure(c,'arts',art_id)
    
    with conn() as c: fields=[dict(x) for x in form_fields(c,art['company_id']) if x['active']]
    for f in fields: f['choices']=json.loads(f['choices_json'])
    return view(req,'change_form.html',art=art,fields=fields)
@app.post('/artes/{art_id}/cambios/nuevo')
async def change_create(req:Request,art_id:int,csrf_token:str=Form(...),title:str=Form(...),reason:str=Form(...),approval_type:str=Form(...),na_justification:str=Form('')):
    u=require(req);csrf(req,csrf_token)
    cid=art_access(u,art_id)
    module_access(u,cid,'solicitudes')
    if approval_type not in ('Interna','Sanitaria'): raise HTTPException(400,'Tipo inválido')
    with conn() as c:
        ensure(c,'arts',art_id)
        cur=c.execute('INSERT INTO changes(art_id,title,reason,approval_type,na_justification,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(art_id,title.strip(),reason.strip(),approval_type,na_justification.strip(),u['id'],utc(),utc()))
        audit(c,u['id'],'CREAR','Cambio',cur.lastrowid,new={'title':title,'approval_type':approval_type})
        id=cur.lastrowid
        form=await req.form()
        art=ensure(c,'arts',art_id)
        for field in form_fields(c,art['company_id']):
            if not field['active']: continue
            value=str(form.get(f'campo_{field["id"]}','')).strip()
            if field['required'] and not value: raise HTTPException(400,f'Campo obligatorio: {field["label"]}')
            if field['field_type']=='si_no_na' and value not in ('','Sí','No','N/A'): raise HTTPException(400,'Opción inválida')
            if field['field_type']=='lista' and value not in ['']+json.loads(field['choices_json']): raise HTTPException(400,'Opción inválida')
            if value: c.execute('INSERT INTO form_answers(change_id,field_id,answer) VALUES(?,?,?)',(id,field['id'],value[:3000]))
    return go(f'/cambios/{id}')
@app.get('/cambios/{change_id}')
def change_detail(req:Request,change_id:int):
    require(req)
    cid=change_access(require(req),change_id)
    module_access(require(req),cid,'solicitudes')
    with conn() as c:
        ch=c.execute('SELECT x.*,a.master_code,u.email creator FROM changes x JOIN arts a ON a.id=x.art_id JOIN users u ON u.id=x.created_by WHERE x.id=?',(change_id,)).fetchone()
        if not ch: raise HTTPException(404,'Control de cambios no encontrado')
        diffs=c.execute('SELECT * FROM change_diffs WHERE change_id=? ORDER BY id DESC',(change_id,)).fetchall()
        approvals=c.execute('SELECT ap.*,u.email FROM approvals ap JOIN users u ON u.id=ap.actor_id WHERE ap.change_id=? ORDER BY ap.id DESC',(change_id,)).fetchall()
        answers=c.execute('SELECT ff.label,fa.answer FROM form_answers fa JOIN form_fields ff ON ff.id=fa.field_id WHERE fa.change_id=? ORDER BY ff.sort_order,ff.id',(change_id,)).fetchall()
    return view(req,'change_detail.html',ch=ch,diffs=diffs,approvals=approvals,answers=answers)
@app.post('/cambios/{change_id}/afectacion')
def add_diff(req:Request,change_id:int,csrf_token:str=Form(...),field_name:str=Form(...),old_value:str=Form(''),new_value:str=Form(''),comment:str=Form('')):
    u=require(req,'Administrador','Regulatorios','Gestión de Calidad');csrf(req,csrf_token)
    cid=change_access(u,change_id)
    module_access(u,cid,'solicitudes')
    with conn() as c:
        ch=ensure(c,'changes',change_id)
        if ch['status'] in ('Aprobada','Rechazada','Cerrada'): raise HTTPException(400,'El cambio ya está cerrado a edición')
        cur=c.execute('INSERT INTO change_diffs(change_id,field_name,old_value,new_value,comment,created_at) VALUES(?,?,?,?,?,?)',(change_id,field_name,old_value,new_value,comment,utc()))
        audit(c,u['id'],'AÑADIR_AFECTACION','Cambio',change_id,new={'diff_id':cur.lastrowid,'field':field_name})
    return go(f'/cambios/{change_id}')
@app.post('/cambios/{change_id}/enviar')
def submit_change(req:Request,change_id:int,csrf_token:str=Form(...)):
    u=require(req,'Administrador','Regulatorios');csrf(req,csrf_token)
    cid=change_access(u,change_id)
    module_access(u,cid,'solicitudes')
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
    cid=change_access(u,change_id)
    module_access(u,cid,'solicitudes')
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
        records=c.execute('''SELECT cv.*,p.name country,a.master_code,v.revision FROM country_versions cv JOIN countries p ON p.id=cv.country_id JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?) AND cv.transition_until IS NOT NULL AND cv.transition_until<=? AND cv.status='Vigente Transitorio' ORDER BY cv.transition_until''',(require(req)['id'],limit)).fetchall()
        pending=c.execute("SELECT ch.*,a.master_code FROM changes ch JOIN arts a ON a.id=ch.art_id WHERE ch.status LIKE 'Pendiente%' AND a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY ch.id DESC",(require(req)['id'],)).fetchall()
    return view(req,'alerts.html',records=records,pending=pending,today=today.isoformat())
@app.get('/auditoria')
def audits(req:Request):
    require(req,'Administrador')
    with conn() as c:
        rows=c.execute('''SELECT au.*,u.email FROM audit au LEFT JOIN users u ON u.id=au.actor_id
            WHERE (au.entity='Arte' AND au.entity_id IN (SELECT id FROM arts WHERE company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            OR (au.entity='Cambio' AND au.entity_id IN (SELECT ch.id FROM changes ch JOIN arts a ON a.id=ch.art_id WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            OR (au.entity='Version' AND au.entity_id IN (SELECT v.id FROM versions v JOIN arts a ON a.id=v.art_id WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            OR (au.entity='VersionPorPais' AND au.entity_id IN (SELECT cv.id FROM country_versions cv JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            OR (au.entity='Adjunto' AND au.entity_id IN (SELECT id FROM attachments WHERE company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            OR (au.entity='CampoFormulario' AND au.entity_id IN (SELECT id FROM form_fields WHERE company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)))
            ORDER BY au.id DESC LIMIT 250''',(require(req)['id'],)*6).fetchall()
    return view(req,'audit.html',rows=rows)
@app.get('/usuarios')
def users(req:Request):
    require(req,'Administrador')
    with conn() as c: rows=c.execute('SELECT DISTINCT u.id,u.email,u.role,u.active,u.created_at FROM users u JOIN user_companies uc ON uc.user_id=u.id WHERE uc.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY u.id',(require(req)['id'],)).fetchall()
    with conn() as c: companies=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?)',(require(req)['id'],)).fetchall()
    return view(req,'users.html',rows=rows,roles=ROLES,companies=companies)
@app.post('/usuarios')
def create_user(req:Request,csrf_token:str=Form(...),email:str=Form(...),password:str=Form(...),role:str=Form(...),company_id:int=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token)
    company_access(u,company_id)
    if role not in ROLES or len(password)<12: raise HTTPException(400,'Rol inválido o contraseña menor de 12 caracteres')
    with conn() as c:
        try: cur=c.execute('INSERT INTO users(email,password,role,created_at) VALUES(?,?,?,?)',(email.lower().strip(),hash_pw(password),role,utc()))
        except sqlite3.IntegrityError: raise HTTPException(400,'Correo ya registrado')
        c.execute('INSERT INTO user_companies(user_id,company_id) VALUES(?,?)',(cur.lastrowid,company_id))
        audit(c,u['id'],'CREAR_USUARIO','Usuario',cur.lastrowid,new={'email':email,'role':role,'company_id':company_id})
    return go('/usuarios')
@app.post('/usuarios/{uid}/estado')
def user_state(req:Request,uid:int,csrf_token:str=Form(...),active:int=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token)
    if uid==u['id'] or active not in (0,1): raise HTTPException(400,'Operación no permitida')
    with conn() as c:
        ensure(c,'users',uid)
        if not set(company_ids(u)).intersection({r['company_id'] for r in c.execute('SELECT company_id FROM user_companies WHERE user_id=?',(uid,)).fetchall()}): raise HTTPException(403,'Usuario de otra empresa')
        c.execute('UPDATE users SET active=? WHERE id=?',(active,uid))
        audit(c,u['id'],'CAMBIAR_ACCESO','Usuario',uid,new={'active':active})
    return go('/usuarios')


# ---------- Configuración autoadministrable por empresa ----------
FIELD_TYPES=('texto','texto_largo','numero','fecha','lista','si_no_na')
MODULE_LABELS={'artes':'Artes maestros','solicitudes':'Solicitudes de cambio','versiones':'Versiones por país','proveedores':'Confirmación de proveedores','alertas':'Alertas','auditoria':'Auditoría','adjuntos':'Archivos adjuntos'}

def form_fields(c,cid):
    return c.execute("SELECT * FROM form_fields WHERE company_id=? AND form_key='solicitud' ORDER BY sort_order,id",(cid,)).fetchall()

@app.get('/configuracion')
def configuration(req:Request,company_id:int=0):
    u=require(req,'Administrador')
    ids=company_ids(u)
    if not ids: raise HTTPException(403,'Sin empresas asignadas')
    cid=company_id or ids[0]
    company_access(u,cid)
    with conn() as c:
        companies=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?)',(u['id'],)).fetchall()
        fields=form_fields(c,cid)
        modules=c.execute('SELECT * FROM company_modules WHERE company_id=? ORDER BY module_key',(cid,)).fetchall()
        storage=c.execute('SELECT * FROM storage_settings WHERE company_id=?',(cid,)).fetchone()
    return view(req,'config.html',companies=companies,cid=cid,fields=fields,modules=modules,labels=MODULE_LABELS,storage=storage,field_types=FIELD_TYPES)

@app.post('/configuracion/campo')
def field_add(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),label:str=Form(...),field_type:str=Form(...),choices:str=Form(''),required:int=Form(0)):
    u=require(req,'Administrador');csrf(req,csrf_token);company_access(u,company_id)
    if not 1<=len(label.strip())<=120 or field_type not in FIELD_TYPES or required not in (0,1): raise HTTPException(400,'Campo inválido')
    items=[x.strip() for x in choices.splitlines() if x.strip()][:100]
    if field_type=='lista' and not items: raise HTTPException(400,'La lista necesita opciones')
    with conn() as c:
        n=c.execute('SELECT COALESCE(MAX(sort_order),0)+10 FROM form_fields WHERE company_id=?',(company_id,)).fetchone()[0]
        cur=c.execute('INSERT INTO form_fields(company_id,label,field_type,choices_json,required,sort_order,created_at) VALUES(?,?,?,?,?,?,?)',(company_id,label.strip(),field_type,json.dumps(items,ensure_ascii=False),required,n,utc()))
        audit(c,u['id'],'AGREGAR_CAMPO','CampoFormulario',cur.lastrowid,new={'company_id':company_id,'label':label})
    return go('/configuracion?company_id='+str(company_id))

@app.post('/configuracion/campo/{field_id}')
def field_edit(req:Request,field_id:int,csrf_token:str=Form(...),label:str=Form(...),active:int=Form(...),required:int=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token)
    with conn() as c:
        row=c.execute('SELECT * FROM form_fields WHERE id=?',(field_id,)).fetchone()
        if not row: raise HTTPException(404,'Campo inexistente')
        company_access(u,row['company_id'])
        if active not in (0,1) or required not in (0,1) or not label.strip(): raise HTTPException(400,'Valor inválido')
        # Desactivar no destruye las respuestas históricas.
        c.execute('UPDATE form_fields SET label=?,active=?,required=? WHERE id=?',(label.strip()[:120],active,required,field_id))
        audit(c,u['id'],'MODIFICAR_CAMPO','CampoFormulario',field_id,old={'label':row['label'],'active':row['active']},new={'label':label,'active':active,'required':required})
    return go('/configuracion?company_id='+str(row['company_id']))

@app.post('/configuracion/modulo')
def module_edit(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),module_key:str=Form(...),enabled:int=Form(...)):
    u=require(req,'Administrador');csrf(req,csrf_token);company_access(u,company_id)
    if module_key not in MODULE_LABELS or enabled not in (0,1): raise HTTPException(400,'Módulo inválido')
    with conn() as c:
        c.execute('UPDATE company_modules SET enabled=? WHERE company_id=? AND module_key=?',(enabled,company_id,module_key))
        audit(c,u['id'],'CONFIGURAR_MODULO','Empresa',company_id,new={'module':module_key,'enabled':enabled})
    return go('/configuracion?company_id='+str(company_id))

@app.post('/configuracion/almacenamiento')
def storage_edit(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),destination:str=Form(...),drive_folder_id:str=Form('')):
    u=require(req,'Administrador');csrf(req,csrf_token);company_access(u,company_id)
    if destination not in ('local','drive'): raise HTTPException(400,'Tipo inválido')
    if destination=='drive' and not drive_folder_id.strip(): raise HTTPException(400,'Indique la carpeta de Drive')
    # Para Drive, las credenciales son gestionadas por entorno, nunca por formulario web.
    with conn() as c:
        c.execute('UPDATE storage_settings SET destination=?,drive_folder_id=? WHERE company_id=?',(destination,drive_folder_id.strip()[:160],company_id))
        audit(c,u['id'],'CAMBIAR_ALMACENAMIENTO','Empresa',company_id,new={'destination':destination})
    return go('/configuracion?company_id='+str(company_id))

# ---------- Adjuntos (local o Drive) ----------
ALLOWED_EXT={'.pdf':('application/pdf',),'.png':('image/png',),'.jpg':('image/jpeg',),'.jpeg':('image/jpeg',),'.docx':('application/vnd.openxmlformats-officedocument.wordprocessingml.document',),'.xlsx':('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',)}
MAX_UPLOAD=20*1024*1024

def drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise HTTPException(503,'Instale los extras de Drive: pip install -r requirements-drive.txt')
    f=os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if not f or not Path(f).is_file(): raise HTTPException(503,'Credenciales Drive no configuradas en el servidor')
    credentials=service_account.Credentials.from_service_account_file(f,scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive','v3',credentials=credentials,cache_discovery=False)

@app.post('/adjuntos/subir')
async def upload_attachment(req:Request,csrf_token:str=Form(...),art_id:int=Form(...),file:UploadFile=File(...)):
    u=require(req,'Administrador','Regulatorios','Gestión de Calidad','Compras');csrf(req,csrf_token)
    cid=art_access(u,art_id);module_access(u,cid,'adjuntos')
    filename=Path(file.filename or '').name[:180]
    ext=Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT: raise HTTPException(400,'Archivo no permitido (PDF, PNG, JPG, DOCX, XLSX)')
    payload=await file.read(MAX_UPLOAD+1)
    await file.close()
    if not payload or len(payload)>MAX_UPLOAD: raise HTTPException(413,'Archivo vacío o mayor de 20 MB')
    if ext=='.pdf' and not payload.startswith(b'%PDF-'): raise HTTPException(400,'PDF inválido')
    if ext in ('.docx','.xlsx') and not payload.startswith(b'PK'): raise HTTPException(400,'Office inválido')
    if ext=='.png' and not payload.startswith(b'\x89PNG\r\n\x1a\n'): raise HTTPException(400,'PNG inválido')
    if ext in ('.jpg','.jpeg') and not payload.startswith(b'\xff\xd8'): raise HTTPException(400,'JPG inválido')
    digest=hashlib.sha256(payload).hexdigest()
    with conn() as c:
        st=c.execute('SELECT * FROM storage_settings WHERE company_id=?',(cid,)).fetchone()
    destination=st['destination'] if st else 'local'
    media=ALLOWED_EXT[ext][0]
    if destination=='drive':
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload
        service=drive_service()
        info=service.files().create(body={'name':filename,'parents':[st['drive_folder_id']]},media_body=MediaIoBaseUpload(BytesIO(payload),mimetype=media,resumable=False),fields='id',supportsAllDrives=True).execute()
        storage_key=info['id']
    else:
        storage_key=uuid.uuid4().hex+ext
        (FILES/storage_key).write_bytes(payload)
    with conn() as c:
        cur=c.execute('INSERT INTO attachments(company_id,art_id,filename,content_type,storage_type,storage_key,sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,art_id,filename,media,destination,storage_key,digest,len(payload),u['id'],utc()))
        audit(c,u['id'],'ADJUNTAR','Adjunto',cur.lastrowid,new={'company_id':cid,'art_id':art_id,'sha256':digest,'destination':destination})
    return go('/artes/'+str(art_id))

@app.get('/adjuntos/{attachment_id}')
def attachment_download(req:Request,attachment_id:int):
    u=require(req)
    with conn() as c:
        row=c.execute('SELECT * FROM attachments WHERE id=?',(attachment_id,)).fetchone()
        if not row: raise HTTPException(404,'Archivo inexistente')
        company_access(u,row['company_id'])
        if row['art_id']: art_access(u,row['art_id'])
        audit(c,u['id'],'DESCARGAR','Adjunto',attachment_id)
    if row['storage_type']=='local':
        p=(FILES/row['storage_key']).resolve()
        if p.parent!=FILES.resolve() or not p.is_file(): raise HTTPException(404,'Archivo no encontrado')
        return FileResponse(p,media_type=row['content_type'],filename=row['filename'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    if row['storage_type']=='drive':
        from fastapi.responses import Response
        service=drive_service()
        content=service.files().get_media(fileId=row['storage_key'],supportsAllDrives=True).execute()
        safe=row['filename'].replace('"','').replace('\n','')
        return Response(content=content,media_type=row['content_type'],headers={'Content-Disposition':f'attachment; filename="{safe}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    raise HTTPException(500,'Destino desconocido')

def reset_admin():
    from getpass import getpass
    email=input('Correo del administrador: ').strip().lower()
    one=getpass('Nueva contraseña (mínimo 12 caracteres): ')
    two=getpass('Repita contraseña: ')
    if len(one)<12 or one!=two: sys.exit('No coincide o tiene menos de 12 caracteres')
    with conn() as c:
        r=c.execute("SELECT id FROM users WHERE lower(email)=? AND role='Administrador' AND active=1",(email,)).fetchone()
        if not r: sys.exit('No se encontró el administrador activo')
        c.execute('UPDATE users SET password=? WHERE id=?',(hash_pw(one),r['id']))
        audit(c,r['id'],'RESTABLECER_PASSWORD_CLI','Usuario',r['id'])
    print('Contraseña actualizada; pruebe /login')

def bootstrap():
    print('=== Creación del primer administrador ===')
    from getpass import getpass
    email=input('Correo administrador: ').strip().lower()
    pw=getpass('Contraseña (mínimo 12 caracteres): ')
    if len(pw)<12 or '@' not in email: sys.exit('Correo o contraseña inválidos')
    with conn() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]: sys.exit('Ya existen usuarios. Use la pantalla Usuarios.')
        cur=c.execute('INSERT INTO users(email,password,role,created_at) VALUES(?,?,?,?)',(email,hash_pw(pw),'Administrador',utc()))
        for row in c.execute('SELECT id FROM companies').fetchall(): c.execute('INSERT INTO user_companies(user_id,company_id) VALUES(?,?)',(cur.lastrowid,row['id']))
        audit(c,cur.lastrowid,'BOOTSTRAP','Usuario',cur.lastrowid)
    print('Administrador creado. Ejecute: uvicorn app:app --reload')
from medigray_bloque2 import register as medigray_bloque2_register
medigray_bloque2_register(sys.modules[__name__])

from medigray_bloque3 import register as medigray_bloque3_register
medigray_bloque3_register(sys.modules[__name__])

from medigray_bloque4 import register as medigray_bloque4_register
medigray_bloque4_register(sys.modules[__name__])

from medigray_final import register as medigray_final_register
medigray_final_register(sys.modules[__name__])

# CONTROL_ARTES_HOMOLOGACION_V1
from homologacion_region import montar_homologacion
montar_homologacion(app, conn, require, csrf, view, go, company_access, audit, utc)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='crear-admin': bootstrap()
    elif len(sys.argv)>1 and sys.argv[1]=='cambiar-clave-admin': reset_admin()
    else: print('Use: python app.py crear-admin | cambiar-clave-admin')
