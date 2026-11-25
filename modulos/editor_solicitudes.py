"""Editor seguro para los campos configurables del formulario Solicitudes.
No modifica ni elimina respuestas históricas.
"""
import json
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse

TIPOS = ('texto', 'texto_largo', 'numero', 'fecha', 'lista', 'si_no_na')


def register(app, conn, require, csrf, view, audit, utc, company_ids, company_access):
    def empresa_permitida(req, cid):
        u = require(req, 'Administrador')
        permitidas = company_ids(u)
        if not permitidas:
            raise HTTPException(403, 'No tiene empresas asignadas')
        if cid is None or cid == 0:
            cid = req.session.get('empresa_actual')
            if cid not in permitidas:
                cid = permitidas[0]
        company_access(u, cid)
        return u, cid

    @app.get('/admin/formularios', response_class=HTMLResponse)
    def designer_page(req: Request, company_id: int = 0):
        u, cid = empresa_permitida(req, company_id)
        with conn() as c:
            empresas = c.execute('''SELECT co.id,co.name FROM companies co
                JOIN user_companies uc ON uc.company_id=co.id
                WHERE uc.user_id=? ORDER BY co.name''', (u['id'],)).fetchall()
            fields = c.execute('''SELECT f.*,
                (SELECT COUNT(*) FROM form_answers a WHERE a.field_id=f.id) AS respuestas
                FROM form_fields f WHERE f.company_id=? AND f.form_key='solicitud'
                ORDER BY f.sort_order, f.id''', (cid,)).fetchall()
        entries = []
        for r in fields:
            f = dict(r)
            try:
                f['choices'] = json.loads(f['choices_json'] or '[]')
            except ValueError:
                f['choices'] = []
            entries.append(f)
        return view(req, 'disenador_formularios.html', companies=empresas,
                    cid=cid, fields=entries, tipos=TIPOS)

    @app.post('/admin/formularios/nuevo')
    def designer_new(req: Request, csrf_token: str = Form(...), company_id: int = Form(...),
                     label: str = Form(...), field_type: str = Form(...),
                     required: int = Form(0), choices: str = Form('')):
        u, cid = empresa_permitida(req, company_id)
        csrf(req, csrf_token)
        label = label.strip()
        if len(label) < 2 or len(label) > 120:
            raise HTTPException(400, 'Nombre del campo: entre 2 y 120 caracteres')
        if field_type not in TIPOS or required not in (0, 1):
            raise HTTPException(400, 'Tipo o condición inválidos')
        options = list(dict.fromkeys(x.strip() for x in choices.splitlines() if x.strip()))
        if field_type == 'lista' and (not options or len(options) > 50 or any(len(x)>120 for x in options)):
            raise HTTPException(400, 'La lista necesita entre 1 y 50 opciones de hasta 120 caracteres')
        if field_type != 'lista':
            options = []
        with conn() as c:
            pos = c.execute('SELECT COALESCE(MAX(sort_order),0)+10 FROM form_fields WHERE company_id=? AND form_key=?', (cid,'solicitud')).fetchone()[0]
            cursor = c.execute('''INSERT INTO form_fields(company_id,form_key,label,field_type,choices_json,
                required,active,sort_order,created_at) VALUES(?,?,?,?,?,?,1,?,?)''',
                (cid,'solicitud', label,field_type,json.dumps(options,ensure_ascii=False),required,pos,utc()))
            audit(c,u['id'],'CREAR_CAMPO','CampoFormulario',cursor.lastrowid,new={'company_id':cid,'label':label,'type':field_type})
        return go_page(cid)

    @app.post('/admin/formularios/campo/{field_id}')
    def designer_edit(req: Request, field_id: int, csrf_token: str = Form(...),
                      label: str = Form(...), required: int = Form(...),
                      active: int = Form(...), choices: str = Form('')):
        u = require(req,'Administrador')
        csrf(req,csrf_token)
        label = label.strip()
        if not 2 <= len(label) <= 120 or required not in (0,1) or active not in (0,1):
            raise HTTPException(400, 'Datos de campo inválidos')
        with conn() as c:
            old = c.execute("SELECT * FROM form_fields WHERE id=? AND form_key='solicitud'", (field_id,)).fetchone()
            if old is None:
                raise HTTPException(404, 'Campo inexistente')
            company_access(u, old['company_id'])
            if old['field_type']=='lista':
                options = list(dict.fromkeys(x.strip() for x in choices.splitlines() if x.strip()))
                if not options or len(options)>50 or any(len(x)>120 for x in options):
                    raise HTTPException(400,'La lista requiere 1 a 50 opciones')
            else:
                options = json.loads(old['choices_json'] or '[]')
            c.execute('''UPDATE form_fields SET label=?,required=?,active=?,choices_json=? WHERE id=?''',
                      (label,required,active,json.dumps(options,ensure_ascii=False),field_id))
            audit(c,u['id'],'EDITAR_CAMPO','CampoFormulario',field_id,
                  old={'label':old['label'],'active':old['active'],'required':old['required']},
                  new={'label':label,'active':active,'required':required})
            cid = old['company_id']
        return go_page(cid)

    @app.post('/admin/formularios/campo/{field_id}/orden')
    def designer_order(req:Request,field_id:int,csrf_token:str=Form(...),direction:str=Form(...)):
        u = require(req,'Administrador')
        csrf(req,csrf_token)
        if direction not in ('up','down'):
            raise HTTPException(400,'Movimiento inválido')
        with conn() as c:
            old = c.execute("SELECT * FROM form_fields WHERE id=? AND form_key='solicitud'", (field_id,)).fetchone()
            if old is None:
                raise HTTPException(404,'Campo inexistente')
            cid = old['company_id']
            company_access(u,cid)
            ids = [x['id'] for x in c.execute('''SELECT id FROM form_fields WHERE company_id=? AND form_key='solicitud'
                         ORDER BY sort_order,id''',(cid,)).fetchall()]
            position = ids.index(field_id)
            nxt = position + (-1 if direction=='up' else 1)
            if 0<=nxt<len(ids):
                ids[position],ids[nxt]=ids[nxt],ids[position]
                c.executemany('UPDATE form_fields SET sort_order=? WHERE id=?',
                              [(i*10+10, id) for i,id in enumerate(ids)])
                audit(c,u['id'],'REORDENAR_CAMPOS','Empresa',cid,new={'form':'solicitud','field_id':field_id,'direction':direction})
        return go_page(cid)


def go_page(cid):
    from fastapi.responses import RedirectResponse
    return RedirectResponse('/admin/formularios?company_id='+str(cid), status_code=303)
