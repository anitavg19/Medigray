"""Extensión Medigray Bloque 2: solicitudes, compras, proveedores y expedientes.
Diseñada para v2 sin alterar esquemas existentes; migraciones aditivas.
"""
import hashlib
import uuid
from pathlib import Path
from io import BytesIO
from datetime import date
from fastapi import Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse


def register(m):
    app = m.app
    with m.conn() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS mg2_provider_status (
                id INTEGER PRIMARY KEY,
                country_version_id INTEGER NOT NULL REFERENCES country_versions(id),
                supplier_name TEXT NOT NULL,
                received_revision TEXT NOT NULL DEFAULT '',
                production_revision TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                actor_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS mg2_provider_cv_idx ON mg2_provider_status(country_version_id);
            CREATE TABLE IF NOT EXISTS mg2_version_documents (
                id INTEGER PRIMARY KEY,
                version_id INTEGER NOT NULL REFERENCES versions(id),
                attachment_id INTEGER NOT NULL UNIQUE REFERENCES attachments(id),
                document_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        ''')

    @app.get('/solicitudes', response_class=HTMLResponse)
    def mg2_solicitudes(req:Request, q:str='', company:int=0, status:str=''):
        u=m.require(req)
        if company: m.company_access(u,company); m.module_access(u,company,'solicitudes')
        with m.conn() as c:
            comps=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY name',(u['id'],)).fetchall()
            rows=c.execute('''SELECT ch.id, ch.title,ch.status,ch.approval_type,ch.created_at,
                     a.id art_id,a.sap_code,a.master_code,a.product,co.name company_name,a.company_id
                     FROM changes ch JOIN arts a ON a.id=ch.art_id JOIN companies co ON co.id=a.company_id
                     WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                       AND (?=0 OR a.company_id=?) AND (?='' OR ch.status=?)
                       AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR ch.title LIKE ?)
                     ORDER BY ch.id DESC LIMIT 300''',
                     (u['id'],company,company,status,status,*[f'%{q}%']*3)).fetchall()
        rows=[r for r in rows if _enabled(m,u,r['company_id'],'solicitudes')]
        return m.view(req,'mg2_solicitudes.html',items=rows,companies=comps,q=q,company=company,status=status,statuses=m.STATUS)

    @app.get('/compras', response_class=HTMLResponse)
    def mg2_compras(req:Request,q:str='',company:int=0,country:int=0):
        u=m.require(req)
        if company: m.company_access(u,company); m.module_access(u,company,'artes')
        with m.conn() as c:
            comps=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?)',(u['id'],)).fetchall()
            countries=c.execute('SELECT * FROM countries ORDER BY name').fetchall()
            rows=c.execute('''SELECT a.id art_id,a.sap_code,a.master_code,a.product,a.company_id,co.name company_name,
                        ct.name country_name, cv.country_id,cv.status,cv.local_version,cv.valid_from,
                        v.id version_id, v.revision,
                        (SELECT COUNT(*) FROM country_versions x JOIN versions vx ON vx.id=x.version_id
                         WHERE vx.art_id=a.id AND x.country_id=cv.country_id AND x.status='Vigente Oficial') official_count
                        FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                        JOIN arts a ON a.id=v.art_id JOIN countries ct ON ct.id=cv.country_id
                        JOIN companies co ON co.id=a.company_id
                        WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                          AND (?=0 OR a.company_id=?) AND (?=0 OR cv.country_id=?)
                          AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
                          AND cv.status IN ('Vigente Oficial','Vigente Transitorio')
                        ORDER BY a.sap_code, ct.name, cv.id DESC LIMIT 300''',
                        (u['id'],company,company,country,country,*[f'%{q}%']*3)).fetchall()
        today=date.today().isoformat()
        results=[]
        for r in rows:
            if not _enabled(m,u,r['company_id'],'artes'): continue
            ok=(r['status']=='Vigente Oficial' and r['official_count']==1 and
                bool(r['valid_from']) and r['valid_from']<=today)
            results.append({'item':r,'ok':ok,'label':'Versión oficial vigente' if ok else 'REVISAR / NO AUTORIZAR COMPRA'})
        return m.view(req,'mg2_compras.html',items=results,companies=comps,countries=countries,q=q,company=company,country=country)

    @app.get('/proveedores-control', response_class=HTMLResponse)
    def mg2_proveedores(req:Request,company:int=0,q:str=''):
        u=m.require(req)
        if company: m.company_access(u,company); m.module_access(u,company,'proveedores')
        with m.conn() as c:
            comps=c.execute('SELECT * FROM companies WHERE id IN (SELECT company_id FROM user_companies WHERE user_id=?)',(u['id'],)).fetchall()
            versions=c.execute('''SELECT cv.id cv_id, a.id art_id,a.company_id,a.sap_code,a.product,co.name company_name,
                          p.name country_name,cv.local_version,cv.status
                          FROM country_versions cv JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id
                          JOIN countries p ON p.id=cv.country_id JOIN companies co ON co.id=a.company_id
                          WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                            AND (?=0 OR a.company_id=?) AND (a.sap_code LIKE ? OR a.product LIKE ?)
                          ORDER BY a.id DESC, cv.id DESC LIMIT 300''',
                          (u['id'],company,company,f'%{q}%',f'%{q}%')).fetchall()
            hist=c.execute('''SELECT s.*,a.id art_id,a.company_id,a.sap_code,a.product,co.name company_name,
                             p.name country_name,cv.local_version
                           FROM mg2_provider_status s
                           JOIN country_versions cv ON cv.id=s.country_version_id
                           JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id
                           JOIN companies co ON co.id=a.company_id JOIN countries p ON p.id=cv.country_id
                           WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                             AND (?=0 OR a.company_id=?) AND (a.sap_code LIKE ? OR s.supplier_name LIKE ?)
                           ORDER BY s.id DESC LIMIT 250''',
                           (u['id'],company,company,f'%{q}%',f'%{q}%')).fetchall()
        versions=[v for v in versions if _enabled(m,u,v['company_id'],'proveedores')]
        hist=[h for h in hist if _enabled(m,u,h['company_id'],'proveedores')]
        return m.view(req,'mg2_proveedores.html',companies=comps,versions=versions,items=hist,company=company,q=q,can_edit=u['role'] in ('Administrador','Regulatorios','Compras'))

    @app.post('/proveedores-control/registrar')
    def mg2_provider_add(req:Request,csrf_token:str=Form(...),cv_id:int=Form(...),supplier_name:str=Form(...),
                         received_revision:str=Form(''),production_revision:str=Form(''),state:str=Form(...),
                         evidence:str=Form(''),notes:str=Form('')):
        u=m.require(req,'Administrador','Regulatorios','Compras'); m.csrf(req,csrf_token)
        cid=m.cv_access(u,cv_id); m.module_access(u,cid,'proveedores')
        valid=('Pendiente de envío','Enviado','Recepción confirmada','Prueba pendiente','Prueba aprobada','En producción','Versión anterior','Retirado')
        if state not in valid or not supplier_name.strip() or len(supplier_name)>160:
            raise HTTPException(400,'Proveedor o estado inválido')
        with m.conn() as c:
            cur=c.execute('''INSERT INTO mg2_provider_status(country_version_id,supplier_name,received_revision,
                      production_revision,state,evidence,notes,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                      (cv_id,supplier_name.strip(),received_revision.strip()[:80],production_revision.strip()[:80],
                       state,evidence.strip()[:500],notes.strip()[:2000],u['id'],m.utc()))
            m.audit(c,u['id'],'ESTADO_PROVEEDOR','Proveedor',cur.lastrowid,new={'cv_id':cv_id,'state':state})
        return m.go('/proveedores-control')

    @app.get('/expedientes/version/{version_id}',response_class=HTMLResponse)
    def mg2_expediente(req:Request,version_id:int):
        u=m.require(req)
        cid=m.version_access(u,version_id); m.module_access(u,cid,'adjuntos')
        with m.conn() as c:
            v=c.execute('''SELECT v.*,a.sap_code,a.master_code,a.product,a.company_id,co.name company_name
                 FROM versions v JOIN arts a ON a.id=v.art_id JOIN companies co ON co.id=a.company_id
                 WHERE v.id=?''',(version_id,)).fetchone()
            docs=c.execute('''SELECT d.document_type,t.* FROM mg2_version_documents d
                             JOIN attachments t ON t.id=d.attachment_id
                             WHERE d.version_id=? ORDER BY d.id DESC''',(version_id,)).fetchall()
        return m.view(req,'mg2_expediente.html',v=v,docs=docs,can_edit=u['role'] in ('Administrador','Regulatorios','Gestión de Calidad'))

    @app.post('/expedientes/version/{version_id}/subir')
    async def mg2_subir(req:Request,version_id:int,csrf_token:str=Form(...),
                        document_type:str=Form(...),file:UploadFile=File(...)):
        u=m.require(req,'Administrador','Regulatorios','Gestión de Calidad');m.csrf(req,csrf_token)
        cid=m.version_access(u,version_id);m.module_access(u,cid,'adjuntos')
        allowed_doc=('Arte final','Aprobación','Control de cambio','Prueba de impresión','Proveedor','Otro')
        if document_type not in allowed_doc: raise HTTPException(400,'Tipo de documento inválido')
        filename=Path(file.filename or '').name[:180]
        ext=Path(filename).suffix.lower()
        if ext not in m.ALLOWED_EXT: raise HTTPException(400,'Formato no admitido')
        payload=await file.read(m.MAX_UPLOAD+1);await file.close()
        if not payload or len(payload)>m.MAX_UPLOAD: raise HTTPException(413,'Archivo vacío o mayor que el límite')
        if ext=='.pdf' and not payload.startswith(b'%PDF-'): raise HTTPException(400,'PDF inválido')
        if ext in ('.docx','.xlsx') and not payload.startswith(b'PK'): raise HTTPException(400,'Archivo Office inválido')
        if ext=='.png' and not payload.startswith(b'\x89PNG\r\n\x1a\n'): raise HTTPException(400,'PNG inválido')
        if ext in ('.jpg','.jpeg') and not payload.startswith(b'\xff\xd8'): raise HTTPException(400,'Imagen JPG inválida')
        with m.conn() as c:
            v=c.execute('SELECT * FROM versions WHERE id=?',(version_id,)).fetchone()
            storage=c.execute('SELECT * FROM storage_settings WHERE company_id=?',(cid,)).fetchone()
        dest=storage['destination'] if storage else 'local'
        if dest=='drive':
            try:
                from googleapiclient.http import MediaIoBaseUpload
                service=m.drive_service()
                info=service.files().create(body={'name':filename,'parents':[storage['drive_folder_id']]},
                     media_body=MediaIoBaseUpload(BytesIO(payload),mimetype=m.ALLOWED_EXT[ext][0],resumable=False),
                     fields='id',supportsAllDrives=True).execute()
                key=info['id']
            except ImportError: raise HTTPException(503,'Instale requirements-drive.txt')
        else:
            key=uuid.uuid4().hex+ext
            (m.FILES/key).write_bytes(payload)
        digest=hashlib.sha256(payload).hexdigest()
        with m.conn() as c:
            cur=c.execute('''INSERT INTO attachments(company_id,art_id,filename,content_type,storage_type,storage_key,
                       sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (cid,v['art_id'],filename,m.ALLOWED_EXT[ext][0],dest,key,digest,len(payload),u['id'],m.utc()))
            c.execute('INSERT INTO mg2_version_documents(version_id,attachment_id,document_type,created_at) VALUES(?,?,?,?)',
                      (version_id,cur.lastrowid,document_type,m.utc()))
            m.audit(c,u['id'],'DOCUMENTO_VERSION','Version',version_id,new={'sha256':digest,'doc_type':document_type})
        return m.go(f'/expedientes/version/{version_id}')


def _enabled(m,u,cid,module):
    try:
        m.module_access(u,cid,module)
        return True
    except HTTPException:
        return False
