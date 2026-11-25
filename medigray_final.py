"""Extensión consolidada Medigray: catálogo, formularios, alertas y diagnóstico.
No altera tablas preexistentes; agrega mgf_products, mgf_supplier_catalog, mgf_form_snapshots.
"""
from datetime import date, timedelta
import json
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse


def register(m):
    app=m.app
    with m.conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS mgf_products(
          id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
          sap_code TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          UNIQUE(company_id,sap_code));
        CREATE TABLE IF NOT EXISTS mgf_supplier_catalog(
          id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
          name TEXT NOT NULL, contact TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, UNIQUE(company_id,name));
        CREATE TABLE IF NOT EXISTS mgf_form_snapshots(
          change_id INTEGER PRIMARY KEY REFERENCES changes(id), fields_json TEXT NOT NULL,
          created_at TEXT NOT NULL);
        ''')

    def companies(u):
        with m.conn() as c:
            return c.execute('''SELECT id,name FROM companies WHERE id IN
                 (SELECT company_id FROM user_companies WHERE user_id=?) ORDER BY name''',(u['id'],)).fetchall()

    @app.get('/catalogos',response_class=HTMLResponse)
    def mgf_catalogs(req:Request,company:int=0):
        u=m.require(req)
        co=companies(u)
        if company:m.company_access(u,company)
        with m.conn() as c:
            products=c.execute('''SELECT p.*,co.name company_name FROM mgf_products p JOIN companies co ON co.id=p.company_id
                 WHERE p.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                 AND (?=0 OR p.company_id=?) ORDER BY p.id DESC LIMIT 300''',(u['id'],company,company)).fetchall()
            suppliers=c.execute('''SELECT p.*,co.name company_name FROM mgf_supplier_catalog p JOIN companies co ON co.id=p.company_id
                 WHERE p.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                 AND (?=0 OR p.company_id=?) ORDER BY p.id DESC LIMIT 300''',(u['id'],company,company)).fetchall()
        return m.view(req,'mgf_catalogos.html',products=products,suppliers=suppliers,companies=co,company=company,can_edit=u['role'] in ('Administrador','Regulatorios'))

    @app.post('/catalogos/producto')
    def mgf_product_add(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),sap_code:str=Form(...),name:str=Form(...),description:str=Form('')):
        u=m.require(req,'Administrador','Regulatorios');m.csrf(req,csrf_token)
        m.module_access(u,company_id,'artes')
        if not sap_code.strip() or not name.strip():raise HTTPException(400,'SAP y producto requeridos')
        with m.conn() as c:
            existing=c.execute('SELECT 1 FROM mgf_products WHERE company_id=? AND sap_code=?',(company_id,sap_code.strip())).fetchone()
            if existing:raise HTTPException(409,'Ya existe este código SAP en el catálogo de esta empresa')
            cur=c.execute('INSERT INTO mgf_products(company_id,sap_code,name,description,created_at) VALUES(?,?,?,?,?)',
                          (company_id,sap_code.strip()[:80],name.strip()[:180],description.strip()[:500],m.utc()))
            m.audit(c,u['id'],'CREAR_PRODUCTO','Producto',cur.lastrowid)
        return m.go('/catalogos?company='+str(company_id))

    @app.post('/catalogos/proveedor')
    def mgf_supplier_add(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),name:str=Form(...),contact:str=Form('')):
        u=m.require(req,'Administrador','Regulatorios');m.csrf(req,csrf_token)
        m.module_access(u,company_id,'proveedores')
        if not name.strip():raise HTTPException(400,'Nombre de proveedor requerido')
        with m.conn() as c:
            if c.execute('SELECT 1 FROM mgf_supplier_catalog WHERE company_id=? AND name=?',(company_id,name.strip())).fetchone():
                raise HTTPException(409,'Proveedor ya registrado en esta empresa')
            cur=c.execute('INSERT INTO mgf_supplier_catalog(company_id,name,contact,created_at) VALUES(?,?,?,?)',
                          (company_id,name.strip()[:160],contact.strip()[:180],m.utc()))
            m.audit(c,u['id'],'CREAR_PROVEEDOR','Proveedor',cur.lastrowid)
        return m.go('/catalogos?company='+str(company_id))

    @app.post('/catalogos/{kind}/{item_id}/estado')
    def mgf_catalog_state(req:Request,kind:str,item_id:int,csrf_token:str=Form(...),active:int=Form(...)):
        u=m.require(req,'Administrador');m.csrf(req,csrf_token)
        if kind not in ('producto','proveedor') or active not in (0,1):raise HTTPException(400,'Operación inválida')
        table={'producto':'mgf_products','proveedor':'mgf_supplier_catalog'}[kind]
        with m.conn() as c:
            item=c.execute(f'SELECT company_id FROM {table} WHERE id=?',(item_id,)).fetchone()
            if not item:raise HTTPException(404,'Registro no encontrado')
            m.company_access(u,item['company_id'])
            c.execute(f'UPDATE {table} SET active=? WHERE id=?',(active,item_id))
            m.audit(c,u['id'],'CAMBIAR_ESTADO_CATALOGO',kind,item_id,new={'active':active})
        return m.go('/catalogos?company='+str(item['company_id']))

    @app.get('/seguimiento',response_class=HTMLResponse)
    def mgf_followup(req:Request,company:int=0):
        u=m.require(req);co=companies(u)
        if company:m.company_access(u,company)
        today=date.today();limit=(today+timedelta(days=30)).isoformat()
        with m.conn() as c:
            transitions=c.execute('''SELECT a.sap_code,a.product,co.name company_name,ct.name country_name,
                    cv.transition_until,cv.status,v.revision,a.company_id
                    FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                    JOIN arts a ON a.id=v.art_id JOIN companies co ON co.id=a.company_id
                    JOIN countries ct ON ct.id=cv.country_id
                    WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                    AND (?=0 OR a.company_id=?) AND cv.status='Vigente Transitorio'
                    AND cv.transition_until IS NOT NULL AND cv.transition_until<=?
                    ORDER BY cv.transition_until LIMIT 250''',(u['id'],company,company,limit)).fetchall()
            pending=c.execute('''SELECT ch.id,ch.title,ch.status,ch.created_at,a.sap_code,co.name company_name,a.company_id
                    FROM changes ch JOIN arts a ON a.id=ch.art_id JOIN companies co ON co.id=a.company_id
                    WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                    AND (?=0 OR a.company_id=?) AND ch.status IN ('Pendiente interna','Pendiente sanitaria')
                    ORDER BY ch.created_at LIMIT 250''',(u['id'],company,company)).fetchall()
            provider=[]
            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mg2_provider_status'").fetchone():
                provider=c.execute('''SELECT s.supplier_name,s.state,s.received_revision,s.production_revision,
                    a.sap_code,co.name company_name,a.company_id
                    FROM mg2_provider_status s JOIN country_versions cv ON cv.id=s.country_version_id
                    JOIN versions v ON v.id=cv.version_id JOIN arts a ON a.id=v.art_id
                    JOIN companies co ON co.id=a.company_id
                    WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                    AND (?=0 OR a.company_id=?)
                    AND s.state IN ('Versión anterior','Pendiente de envío','Prueba pendiente')
                    ORDER BY s.id DESC LIMIT 250''',(u['id'],company,company)).fetchall()
        # No revelar información de módulos desactivados.
        def enabled(cid,key):
            try:m.module_access(u,cid,key);return True
            except HTTPException:return False
        transitions=[x for x in transitions if enabled(x['company_id'],'versiones')]
        pending=[x for x in pending if enabled(x['company_id'],'solicitudes')]
        provider=[x for x in provider if enabled(x['company_id'],'proveedores')]
        return m.view(req,'mgf_seguimiento.html',companies=co,company=company,transitions=transitions,pending=pending,provider=provider,today=today.isoformat())

    @app.get('/administracion/resumen',response_class=HTMLResponse)
    def mgf_admin(req:Request,company:int=0):
        u=m.require(req,'Administrador');co=companies(u)
        cid=company or (co[0]['id'] if co else 0)
        if not cid:raise HTTPException(403,'Sin empresas')
        m.company_access(u,cid)
        with m.conn() as c:
            fields=c.execute('SELECT id,label,field_type,required,active,sort_order FROM form_fields WHERE company_id=? ORDER BY sort_order,id',(cid,)).fetchall()
            modules=c.execute('SELECT module_key,enabled FROM company_modules WHERE company_id=? ORDER BY module_key',(cid,)).fetchall()
            storage=c.execute('SELECT destination,drive_folder_id FROM storage_settings WHERE company_id=?',(cid,)).fetchone()
            totals={t:c.execute(f'SELECT COUNT(*) FROM {t} WHERE company_id=?',(cid,)).fetchone()[0] for t in ('arts','mgf_products','mgf_supplier_catalog')}
        return m.view(req,'mgf_admin.html',companies=co,cid=cid,fields=fields,modules=modules,storage=storage,totals=totals)
