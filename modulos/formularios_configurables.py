"""Editor de campos adicionales por formulario y empresa. No borra respuestas históricas."""
import json
from fastapi import Request, Form, HTTPException
from fastapi.responses import RedirectResponse

FORMULARIOS = {'arte': 'Registro de arte', 'solicitud': 'Solicitud de control de cambios'}
TIPOS = ('texto','texto_largo','numero','fecha','lista','si_no_na','enlace')

def _opts(txt):
    options = list(dict.fromkeys(s.strip() for s in txt.splitlines() if s.strip()))
    if len(options) > 50 or any(len(s)>120 for s in options): raise HTTPException(400, 'Demasiadas opciones o demasiado largas')
    return options

def campos(c, company_id, form_key, solo_activos=True):
    if form_key not in FORMULARIOS: raise HTTPException(400,'Formulario inválido')
    sql = 'SELECT * FROM form_fields WHERE company_id=? AND form_key=?'
    if solo_activos: sql += ' AND active=1'
    return [dict(r) for r in c.execute(sql+' ORDER BY sort_order,id', (company_id,form_key)).fetchall()]

def guardar_campos_arte(c, art_id, company_id, form):
    for f in campos(c, company_id, 'arte'):
        value = str(form.get('campo_'+str(f['id']), '')).strip()
        if f['required'] and not value: raise HTTPException(400, f'Campo obligatorio: {f["label"]}')
        if f['field_type']=='si_no_na' and value not in ('','Sí','No','N/A'): raise HTTPException(400,'Opción inválida')
        if f['field_type']=='lista' and value not in ['']+json.loads(f['choices_json'] or '[]'): raise HTTPException(400,'Opción inválida')
        if f['field_type']=='numero' and value:
            try: float(value)
            except ValueError: raise HTTPException(400, f'Número inválido: {f["label"]}')
        if f['field_type']=='enlace' and value and not value.startswith('https://'): raise HTTPException(400, f'Enlace HTTPS requerido: {f["label"]}')
        if value:
            c.execute('INSERT INTO art_field_answers(art_id,field_id,answer) VALUES(?,?,?)', (art_id,f['id'],value[:3000]))

def registrar(app, conn, require, csrf, view, audit, utc, company_ids, company_access):
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS art_field_answers (
            art_id INTEGER NOT NULL REFERENCES arts(id),
            field_id INTEGER NOT NULL REFERENCES form_fields(id),
            answer TEXT NOT NULL, PRIMARY KEY(art_id,field_id))''')

    def ctx(req, cid=0):
        u=require(req,'Administrador')
        allowed=company_ids(u)
        if not allowed: raise HTTPException(403,'Sin empresas asignadas')
        if cid==0: cid=req.session.get('empresa_actual')
        if cid not in allowed: raise HTTPException(403,'Seleccione una empresa a la que tenga acceso')
        company_access(u,cid)
        return u,cid

    def back(cid, key):
        return RedirectResponse(f'/admin/formularios-general?company_id={cid}&form_key={key}',status_code=303)

    @app.get('/admin/formularios-general')
    def editor(req:Request,company_id:int=0,form_key:str='arte'):
        u,cid=ctx(req,company_id)
        if form_key not in FORMULARIOS: raise HTTPException(400,'Formulario inválido')
        with conn() as c:
            rows=campos(c,cid,form_key,False)
            empresas=c.execute('''SELECT co.id,co.name FROM companies co JOIN user_companies uc ON co.id=uc.company_id
                WHERE uc.user_id=? ORDER BY co.name''',(u['id'],)).fetchall()
            for f in rows:
                f['respuestas'] = c.execute('SELECT COUNT(*) FROM '+ ('art_field_answers' if form_key=='arte' else 'form_answers')+' WHERE field_id=?',(f['id'],)).fetchone()[0]
                f['options_text']='\n'.join(json.loads(f['choices_json'] or '[]'))
        return view(req,'formularios_general.html',campos=rows,empresas=empresas,cid=cid,form_key=form_key,
                    nombres=FORMULARIOS,tipos=TIPOS)

    @app.post('/admin/formularios-general/crear')
    def crear(req:Request,csrf_token:str=Form(...),company_id:int=Form(...),form_key:str=Form(...),
              label:str=Form(...),field_type:str=Form(...),required:int=Form(0),choices:str=Form('')):
        u,cid=ctx(req,company_id);csrf(req,csrf_token)
        label=label.strip()
        if form_key not in FORMULARIOS or field_type not in TIPOS or required not in (0,1) or not 2<=len(label)<=120:
            raise HTTPException(400,'Campo inválido')
        opts=_opts(choices) if field_type=='lista' else []
        if field_type=='lista' and not opts: raise HTTPException(400,'Agregue opciones a la lista')
        with conn() as c:
            pos=c.execute('SELECT COALESCE(MAX(sort_order),0)+10 FROM form_fields WHERE company_id=? AND form_key=?',(cid,form_key)).fetchone()[0]
            cur=c.execute('''INSERT INTO form_fields(company_id,form_key,label,field_type,choices_json,required,active,sort_order,created_at)
                VALUES(?,?,?,?,?,?,1,?,?)''',(cid,form_key,label,field_type,json.dumps(opts,ensure_ascii=False),required,pos,utc()))
            audit(c,u['id'],'CREAR_CAMPO','CampoFormulario',cur.lastrowid,new={'form':form_key,'company':cid,'label':label})
        return back(cid,form_key)

    @app.post('/admin/formularios-general/editar/{fid}')
    def editar(req:Request,fid:int,csrf_token:str=Form(...),label:str=Form(...),
               required:int=Form(...),active:int=Form(...),choices:str=Form('')):
        u=require(req,'Administrador');csrf(req,csrf_token)
        if required not in (0,1) or active not in (0,1) or not 2<=len(label.strip())<=120:
            raise HTTPException(400,'Valores inválidos')
        with conn() as c:
            old=c.execute('SELECT * FROM form_fields WHERE id=?',(fid,)).fetchone()
            if old is None or old['form_key'] not in FORMULARIOS: raise HTTPException(404,'Campo no encontrado')
            company_access(u,old['company_id'])
            opts=_opts(choices) if old['field_type']=='lista' else json.loads(old['choices_json'] or '[]')
            if old['field_type']=='lista' and not opts: raise HTTPException(400,'Lista sin opciones')
            c.execute('UPDATE form_fields SET label=?,required=?,active=?,choices_json=? WHERE id=?',
                      (label.strip(),required,active,json.dumps(opts,ensure_ascii=False),fid))
            audit(c,u['id'],'EDITAR_CAMPO','CampoFormulario',fid,
                  old={'label':old['label'],'active':old['active']},new={'label':label,'active':active})
            cid=old['company_id'];key=old['form_key']
        return back(cid,key)

    @app.post('/admin/formularios-general/orden/{fid}')
    def orden(req:Request,fid:int,csrf_token:str=Form(...),direction:str=Form(...)):
        u=require(req,'Administrador');csrf(req,csrf_token)
        if direction not in ('up','down'): raise HTTPException(400,'Movimiento inválido')
        with conn() as c:
            old=c.execute('SELECT * FROM form_fields WHERE id=?',(fid,)).fetchone()
            if old is None or old['form_key'] not in FORMULARIOS: raise HTTPException(404,'Campo inexistente')
            cid=old['company_id'];key=old['form_key'];company_access(u,cid)
            ids=[r['id'] for r in campos(c,cid,key,False)]
            index=ids.index(fid);next_index=index + (-1 if direction=='up' else 1)
            if 0<=next_index<len(ids):
                ids[index],ids[next_index]=ids[next_index],ids[index]
                c.executemany('UPDATE form_fields SET sort_order=? WHERE id=?',[(10*(i+1),ident) for i,ident in enumerate(ids)])
                audit(c,u['id'],'ORDENAR_CAMPOS','Empresa',cid,new={'form':key,'campo':fid})
        return back(cid,key)
