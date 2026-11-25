"""Configuracion de PRESENTACION de campos existentes; no altera columnas ni validaciones."""
import json
from fastapi import Request, Form, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

CORE = {
    'arte': [
        ('company_id', 'Empresa', True),
        ('master_code', 'Código maestro', True),
        ('sap_code', 'Código SAP', True),
        ('product', 'Producto', True),
        ('packaging_type', 'Tipo de empaque', True),
        ('description', 'Descripción', False),
        ('drive_url', 'Enlace de Google Drive', True),
    ],
    'solicitud': [
        ('title', 'Título de la solicitud', True),
        ('reason', 'Motivo del cambio', True),
        ('approval_type', 'Tipo de aprobación', True),
        ('na_justification', 'Justificación N/A', False),
    ]
}


def registrar(app, templates, conn, require, csrf, company_access, art_access, audit):
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS core_form_ui (
          company_id INTEGER NOT NULL REFERENCES companies(id),
          form_key TEXT NOT NULL, field_name TEXT NOT NULL,
          label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(company_id,form_key,field_name))''')

    def ui_core_fields(form_key, company_id):
        defaults = CORE.get(form_key, [])
        with conn() as c:
            custom = {(r['field_name']):dict(r) for r in c.execute(
                'SELECT * FROM core_form_ui WHERE company_id=? AND form_key=?',
                (company_id, form_key)).fetchall()}
        return [dict(name=k, label=custom.get(k,{}).get('label',label),
                     active=bool(custom.get(k,{}).get('active',1)),
                     protected=protected) for k,label,protected in defaults]

    templates.env.globals['ui_core_fields'] = ui_core_fields

    @app.post('/admin/formularios-general/campo-base')
    def cambiar_campo_base(req:Request,csrf_token:str=Form(...), company_id:int=Form(...),
                          form_key:str=Form(...),field_name:str=Form(...),
                          label:str=Form(...),active:int=Form(1)):
        u=require(req,'Administrador'); csrf(req,csrf_token); company_access(u,company_id)
        known={name:protected for name,_,protected in CORE.get(form_key,[])}
        if field_name not in known: raise HTTPException(400,'Campo original no reconocido')
        label=label.strip()
        if not 2<=len(label)<=100 or active not in (0,1):
            raise HTTPException(400,'Nombre o visibilidad inválidos')
        if known[field_name] and active!=1:
            raise HTTPException(400,'Este campo es esencial y no puede ocultarse')
        with conn() as c:
            c.execute('''INSERT INTO core_form_ui(company_id,form_key,field_name,label,active)
                         VALUES(?,?,?,?,?) ON CONFLICT(company_id,form_key,field_name)
                         DO UPDATE SET label=excluded.label, active=excluded.active''',
                         (company_id,form_key,field_name,label,active))
            audit(c,u['id'],'CONFIGURAR_CAMPO_BASE','Empresa',company_id,
                  new={'formulario':form_key,'campo':field_name,'nombre':label,'visible':active})
        return RedirectResponse(f'/admin/formularios-general?company_id={company_id}&form_key={form_key}',status_code=303)

    @app.get('/ui/campos-existentes')
    def schema(req:Request,form_key:str='arte',company_id:int=0,art_id:int=0):
        u=require(req)
        if form_key not in CORE: raise HTTPException(400,'Formulario inválido')
        if form_key=='solicitud':
            if not art_id: raise HTTPException(400,'Debe indicar el arte')
            company_id=art_access(u,art_id)
        else:
            company_access(u,company_id)
        return JSONResponse({'fields':ui_core_fields(form_key,company_id)},
                            headers={'Cache-Control':'no-store'})
