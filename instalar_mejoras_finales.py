"""Instalador incremental. No mueve ni borra storage. Idempotente."""
from pathlib import Path
from datetime import datetime
from jinja2 import Environment
import shutil,sys
r=Path(__file__).resolve().parent
app=r/'app.py';base=r/'templates/base.html';ext=r/'medigray_final.py'
tpls=[r/'mgf_catalogos.html',r/'mgf_seguimiento.html',r/'mgf_admin.html']
if not (app.is_file() and base.is_file() and ext.is_file() and all(x.is_file() for x in tpls)):
    sys.exit('Faltan archivos. Extraiga TODO el ZIP junto a app.py.')
s=app.read_text(encoding='utf8');b=base.read_text(encoding='utf8')
if 'medigray_final_register' in s: sys.exit('La extensión ya está registrada. No se realizaron modificaciones.')
if 'medigray_bloque2_register' not in s or 'medigray_bloque3_register' not in s or 'medigray_bloque4_register' not in s:
    sys.exit('Falta instalar alguno de los Bloques 2, 3 o 4. No se realizó ninguna modificación.')
anchor="if __name__=='__main__':"
if s.count(anchor)!=1 or '<nav>' not in b:sys.exit('Código base incompatible. No se modificaron archivos.')
s=s.replace(anchor,'from medigray_final import register as medigray_final_register\nmedigray_final_register(sys.modules[__name__])\n\n'+anchor,1)
for href,label in [('/catalogos','▤ Productos y proveedores'),('/seguimiento','◷ Seguimiento operativo')]:
    if f'href="{href}"' not in b:b=b.replace('<nav>',f'<nav><a href="{href}">{label}</a>',1)
if 'href="/administracion/resumen"' not in b:
    b=b.replace('<a href="/configuracion">','<a href="/administracion/resumen">▣ Administración avanzada</a><a href="/configuracion">',1)
# La auditoría sigue guardándose, pero se oculta su enlace si está presente.
b=b.replace('<a href="/auditoria">≡ Auditoría</a>','')
compile(s,str(app),'exec');compile(ext.read_text(encoding='utf8'),str(ext),'exec')
for content in [b]+[x.read_text(encoding='utf8') for x in tpls]:Environment().parse(content)
backup=r/('respaldo_codigo_final_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir();shutil.copy2(app,backup/'app.py');shutil.copy2(base,backup/'base.html')
app.write_text(s,encoding='utf8');base.write_text(b,encoding='utf8')
for x in tpls:shutil.copy2(x,r/'templates'/x.name)
print('MEJORAS FINALES INSTALADAS. Se conservaron storage y credenciales.')
print('Respaldo de código:',backup)
print('Reinicie Uvicorn para registrar las rutas y crear las tablas nuevas.')
