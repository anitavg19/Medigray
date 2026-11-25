from pathlib import Path
from datetime import datetime
from jinja2 import Environment
import shutil, sys
r=Path(__file__).resolve().parent
app=r/'app.py'; base=r/'templates'/'base.html'
if not app.exists() or not base.exists() or not (r/'medigray_bloque3.py').exists() or not (r/'mg3_activacion.html').exists():
    sys.exit('Faltan archivos. Coloque todos los archivos del ZIP junto a app.py.')
s=app.read_text(encoding='utf8'); b=base.read_text(encoding='utf8')
if 'medigray_bloque3_register' in s: sys.exit('Bloque 3 ya instalado; sin modificaciones.')
if s.count("if __name__=='__main__':")!=1 or '<nav>' not in b or 'def cv_access(' not in s:
    sys.exit('app.py/base.html incompatibles. No se ha modificado nada.')
append='from medigray_bloque3 import register as medigray_bloque3_register\nmedigray_bloque3_register(sys.modules[__name__])\n'
s=s.replace("if __name__=='__main__':",append+"\nif __name__=='__main__':",1)
if 'href="/activacion-versiones"' not in b:
    b=b.replace('<nav>','<nav><a href="/activacion-versiones">↺ Activar versiones</a>',1)
compile(s,str(app),'exec');compile((r/'medigray_bloque3.py').read_text(),str(r/'medigray_bloque3.py'),'exec')
Environment().parse(b);Environment().parse((r/'mg3_activacion.html').read_text())
backup=r/('respaldo_codigo_bloque3_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir();shutil.copy2(app,backup/'app.py');shutil.copy2(base,backup/'base.html')
app.write_text(s,encoding='utf8');base.write_text(b,encoding='utf8')
shutil.copy2(r/'mg3_activacion.html',r/'templates'/'mg3_activacion.html')
print('BLOQUE 3 INSTALADO; respaldo:',backup)
