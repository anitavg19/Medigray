"""Instalador incremental del Bloque 4. Nunca toca storage."""
from pathlib import Path
from datetime import datetime
import sys, shutil
from jinja2 import Environment
root=Path(__file__).resolve().parent
app=root/'app.py';base=root/'templates/base.html'
files=[root/'medigray_bloque4.py',root/'mg4_biblioteca.html',root/'mg4_verificacion.html']
if not app.is_file() or not base.is_file() or any(not p.is_file() for p in files):
    sys.exit('Faltan archivos. Coloque los archivos del Bloque 4 junto a app.py.')
s=app.read_text(encoding='utf8');b=base.read_text(encoding='utf8')
if 'medigray_bloque4_register' in s:
    sys.exit('Bloque 4 ya registrado: no se modificó nada.')
if 'medigray_bloque2_register' not in s or 'medigray_bloque3_register' not in s:
    sys.exit('Se necesitan los Bloques 2 y 3 instalados. No se modificó nada.')
anchor="if __name__=='__main__':"
if s.count(anchor)!=1 or '<nav>' not in b or '</nav>' not in b:
    sys.exit('Código base incompatible. No se modificó nada.')
s=s.replace(anchor,'from medigray_bloque4 import register as medigray_bloque4_register\nmedigray_bloque4_register(sys.modules[__name__])\n\n'+anchor,1)
if 'href="/biblioteca-documental"' not in b:
    b=b.replace('<nav>','<nav><a href="/biblioteca-documental">▤ Biblioteca documental</a>',1)
compile(s,str(app),'exec');compile(files[0].read_text(encoding='utf8'),str(files[0]),'exec')
for t in [b,files[1].read_text(encoding='utf8'),files[2].read_text(encoding='utf8')]:Environment().parse(t)
backup=root/('respaldo_codigo_bloque4_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir()
shutil.copy2(app,backup/'app.py');shutil.copy2(base,backup/'base.html')
app.write_text(s,encoding='utf8');base.write_text(b,encoding='utf8')
for p in files[1:]:shutil.copy2(p,root/'templates'/p.name)
print('BLOQUE 4 INSTALADO. Storage, usuarios, contraseñas y versiones intactos.')
print('Respaldo de código:',backup)
