from pathlib import Path
import shutil, datetime, re
ROOT=Path(__file__).resolve().parent
APP=ROOT/'app.py'
FORM=ROOT/'templates'/'art_form.html'
DETAIL=ROOT/'templates'/'art_detail.html'
if not all(p.exists() for p in [APP,FORM,DETAIL]):
    raise SystemExit('No se encontró app.py y las plantillas requeridas en esta carpeta')
s=APP.read_text(encoding='utf-8'); form=FORM.read_text(encoding='utf-8'); detail=DETAIL.read_text(encoding='utf-8')
if 'MEDIGRAY_DOC_ARTE_V1' in s:
    print('La actualización ya está instalada; no se realizaron cambios.'); raise SystemExit(0)
start=s.find("@app.post('/artes/nuevo')")
end=s.find("@app.get('/artes/{art_id}')", start)
if start<0 or end<0 or not "@app.get('/artes/nuevo')" in s:
    raise SystemExit('Estructura incompatible: no se modificó ningún archivo')
if 'return view(req,\'art_detail.html\'' not in s or '{% endblock %}' not in detail:
    raise SystemExit('Plantillas incompatibles: no se modificó ningún archivo')
new_route='''# MEDIGRAY_DOC_ARTE_V1: documento obligatorio al crear el arte maestro.
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
'''
s=s[:start]+new_route+s[end:]
marker='init_db()'
# insert migration right after the standalone init_db invocation
index=s.find('\ninit_db()')
if index<0: raise SystemExit('No se detectó init_db(): cambios cancelados')
index+=len('\ninit_db()')
s=s[:index]+'''
# Documento externo por arte; no guarda credenciales ni copia automática de Drive.
with conn() as _c:
    _c.execute('CREATE TABLE IF NOT EXISTS art_document_links (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), art_id INTEGER NOT NULL REFERENCES arts(id), url TEXT NOT NULL, description TEXT, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)')
'''+s[index:]
old="attachments=c.execute('SELECT * FROM attachments WHERE art_id=? ORDER BY id DESC',(art_id,)).fetchall()"
if old not in s: raise SystemExit('No coincide la consulta de adjuntos: cambios cancelados')
s=s.replace(old,old+"\n        document_links=c.execute('SELECT * FROM art_document_links WHERE art_id=? AND company_id=? ORDER BY id DESC',(art_id,cid)).fetchall()",1)
s=s.replace('status=VSTATUS,attachments=attachments)','status=VSTATUS,attachments=attachments,document_links=document_links)',1)
new_form='''{% extends 'base.html' %}
{% block content %}
<h1>Nuevo arte maestro</h1>
<div class="panel">
<form method="post" action="/artes/nuevo" enctype="multipart/form-data">
<input type="hidden" name="csrf_token" value="{{csrf}}">
<div class="grid">
<div><label>Código maestro único *</label><input name="master_code" required placeholder="ART-000145"></div>
<div><label>Código SAP *</label><input name="sap_code" required placeholder="10004567"></div>
<div><label>Empresa *</label><select name="company_id" required>{% for c in companies %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></div>
<div><label>Producto *</label><input name="product" required></div>
<div><label>Tipo de empaque *</label><select name="packaging_type" required><option>Etiqueta</option><option>Inserto</option><option>Empaque primario</option><option>Empaque secundario</option><option>Otro</option></select></div>
</div>
<label>Descripción</label><textarea name="description"></textarea>
<div class="panel" style="background:#f8fafc">
<h2>Documento inicial del arte *</h2>
<p class="muted">Debe aportar un PDF/Word o el enlace al documento existente en Google Drive. El enlace NO genera una copia de seguridad automáticamente.</p>
<label>¿Dónde está el documento?</label>
<select name="document_mode" id="doc-mode" onchange="mgDocMode()"><option value="archivo">Subir PDF o Word desde mi computadora</option><option value="drive">Registrar enlace de Google Drive</option></select>
<div id="doc-file"><label>Seleccionar archivo *</label><input type="file" name="document_file" id="doc-upload" accept=".pdf,.docx" required><p class="muted">Máximo 20 MB. El documento inicial se guarda localmente con control de acceso.</p></div>
<div id="doc-drive" style="display:none"><label>Enlace al archivo de Drive *</label><input type="url" name="drive_url" id="doc-url" placeholder="https://drive.google.com/file/d/..." ><p class="muted">Los permisos del archivo deben gestionarse también en Google Drive.</p></div>
</div>
<button class="btn" type="submit">Guardar arte y documento</button> <a href="/" class="btn gray">Cancelar</a>
</form>
</div>
<script>
function mgDocMode(){const drive=document.getElementById('doc-mode').value==='drive';document.getElementById('doc-file').style.display=drive?'none':'block';document.getElementById('doc-drive').style.display=drive?'block':'none';document.getElementById('doc-upload').required=!drive;document.getElementById('doc-url').required=drive;}
mgDocMode();
</script>
{% endblock %}
'''
new_detail='''<section class="panel"><h2>Documentos iniciales del arte</h2>
{% for d in attachments %}<p>📄 <a href="/adjuntos/{{d.id}}">{{d.filename}}</a> <small>({{d.storage_type}})</small></p>{% endfor %}
{% for l in document_links %}<p>🔗 <a href="{{l.url}}" target="_blank" rel="noopener noreferrer">{{l.description or 'Abrir en Drive'}}</a> <small>(Enlace externo; se aplican permisos de Drive)</small></p>{% endfor %}
{% if not attachments and not document_links %}<p class="muted">Arte antiguo sin expediente inicial; adjunte documentación.</p>{% endif %}
</section>
'''
detail=detail.replace('{% endblock %}',new_detail+'{% endblock %}',1)
backup=ROOT/'respaldo_instalacion_documento_arte'
backup.mkdir(exist_ok=True)
for p in (APP,FORM,DETAIL):
    shutil.copy2(p,backup/p.name)
APP.write_text(s,encoding='utf-8');FORM.write_text(new_form,encoding='utf-8');DETAIL.write_text(detail,encoding='utf-8')
try:
    import ast
    ast.parse(s)
    from jinja2 import Environment,FileSystemLoader
    e=Environment(loader=FileSystemLoader(str(ROOT/'templates')))
    e.get_template('art_form.html');e.get_template('art_detail.html')
except Exception as ex:
    for p in (APP,FORM,DETAIL): shutil.copy2(backup/p.name,p)
    raise SystemExit(f'Error de validación; archivos restaurados: {ex}')
print('ACTUALIZACIÓN INSTALADA: documento inicial obligatorio al crear cada arte.')
print('Respaldos de código:', backup)
print('No se eliminó ni reemplazó storage.')
