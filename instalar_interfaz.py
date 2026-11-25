"""Instala únicamente la interfaz de Medigray sin tocar app.py ni storage."""
from pathlib import Path
from datetime import datetime
import shutil
from jinja2 import Environment, FileSystemLoader

root = Path(__file__).resolve().parent
project = root
if not (project / 'app.py').is_file():
    raise SystemExit('ERROR: coloque los archivos extraídos en la misma carpeta de app.py (Medigray).')
target = project / 'templates'
source = root / 'interfaz_nueva'
files = ['base.html', 'home.html', 'mgf_catalogos.html']
missing = [f for f in files if not (source / f).is_file()]
if missing:
    raise SystemExit('Faltan archivos: ' + ', '.join(missing))
if not (target / 'base.html').is_file() or not (target / 'home.html').is_file():
    raise SystemExit('Faltan base.html o home.html del proyecto. No se modificó nada.')
# Validate before touching files.
from jinja2 import Environment
for f in files:
    Environment().parse((source / f).read_text(encoding='utf8'))
backup = project / ('respaldo_interfaz_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir()
for f in files:
    if (target / f).exists():
        shutil.copy2(target / f, backup / f)
for f in files:
    shutil.copy2(source / f, target / f)
print('INTERFAZ INSTALADA CORRECTAMENTE')
print('Respaldos anteriores:', backup)
print('Archivos actualizados:', ', '.join(files))
print('No se modificaron app.py, la base de datos, usuarios ni documentos.')
