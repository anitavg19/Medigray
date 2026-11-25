#!/usr/bin/env python3
"""Instalador conservador; no borra datos ni reemplaza rutas anteriores."""
from pathlib import Path
from datetime import datetime
from jinja2 import Environment
import shutil, sys
root=Path(__file__).resolve().parent
app=root/'app.py'; base=root/'templates'/'base.html'
if not app.exists() or not base.exists(): sys.exit('ERROR: Suba este instalador y medigray_bloque2.py a la carpeta de app.py.')
ext=root/'medigray_bloque2.py'
if not ext.exists(): sys.exit('ERROR: Falta medigray_bloque2.py')
tplsrc=root/'templates_bloque2'
if not tplsrc.exists(): sys.exit('ERROR: Falta templates_bloque2 (carpeta del paquete).')
src=app.read_text(encoding='utf-8'); layout=base.read_text(encoding='utf-8')
required=['def conn():','def module_access(', 'def version_access(', 'def view(', "@app.get('/login'", 'def drive_service():']
if not all(x in src for x in required): sys.exit('ERROR: app.py incompatible; no se cambiaron archivos.')
if '<nav>' not in layout or '</nav>' not in layout: sys.exit('ERROR: base.html incompatible; no se cambiaron archivos.')
marker='from medigray_bloque2 import register as medigray_bloque2_register\nmedigray_bloque2_register(sys.modules[__name__])\n'
# Registrar rutas antes del bloque __main__: garantiza que funcione con uvicorn y CLI.
anchor="if __name__=='__main__':"
if src.count(anchor)!=1: sys.exit('ERROR: No se localizó el bloque __main__.')
if 'medigray_bloque2_register' in src: sys.exit('El bloque 2 ya está instalado; no se realizaron cambios.')
updated=src.replace(anchor,marker+'\n'+anchor,1)
links='''<a href="/solicitudes">☷ Solicitudes registradas</a><a href="/compras">▣ Compras</a><a href="/proveedores-control">▤ Proveedores</a>'''
layout=layout.replace('<nav>','<nav>'+links,1)
compile(updated,str(app),'exec');compile(ext.read_text(encoding='utf-8'),str(ext),'exec')
for t in [layout]+[p.read_text(encoding='utf-8') for p in tplsrc.glob('*.html')]: Environment().parse(t)
backup=root/('respaldo_bloque2_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir();shutil.copy2(app,backup/'app.py');shutil.copy2(base,backup/'base.html')
for p in tplsrc.glob('*.html'):
    destination=root/'templates'/p.name
    if destination.exists(): shutil.copy2(destination,backup/p.name)
app.write_text(updated,encoding='utf-8');base.write_text(layout,encoding='utf-8')
for p in tplsrc.glob('*.html'): shutil.copy2(p,root/'templates'/p.name)
print('BLOQUE 2 INSTALADO. NO se borró storage ni se modificaron contraseñas.')
print('Reinicie Uvicorn; al iniciar se crearán 2 tablas adicionales.')
print('Respaldo de código:',backup)
