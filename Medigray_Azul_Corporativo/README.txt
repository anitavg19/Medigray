MEDIGRAY — ACTUALIZACION VISUAL AZUL CORPORATIVO

Modifica únicamente templates/base.html y templates/home.html.
No modifica app.py, storage/, usuarios, contraseñas, ni los enlaces de Drive.
Identidad: MEDIGRAY / Inversiones Oridama (no incluye DR Pharma).

INSTALACION (GitHub Codespaces)
1. Subir Medigray_Azul_Corporativo.zip a /workspaces/Medigray
2. Desde /workspaces/Medigray ejecutar:

mkdir -p respaldos/ui_antes_azul
cp templates/base.html templates/home.html respaldos/ui_antes_azul/
unzip -oq Medigray_Azul_Corporativo.zip
cp Medigray_Azul_Corporativo/templates/base.html templates/base.html
cp Medigray_Azul_Corporativo/templates/home.html templates/home.html
python -c "from jinja2 import Environment,FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); [e.get_template(n) for n in ['base.html','home.html']]; print('Plantillas correctas')"

3. Actualizar navegador Ctrl+Shift+R. Normalmente no hace falta reiniciar Uvicorn.
4. Si necesita deshacer:
cp respaldos/ui_antes_azul/base.html templates/base.html
cp respaldos/ui_antes_azul/home.html templates/home.html

NOTAS:
- El logo de la web publica no está incluido; se conserva un isotipo de texto para no reproducir un archivo sin disponer del original.
- Los enlaces y permisos existentes siguen iguales: el cambio es exclusivamente visual.
- Un enlace a una ruta aún no instalada seguirá sin funcionar. Esta actualización no añade rutas ni funciones nuevas.
