MEDIGRAY — SELECTOR DE EMPRESA

Objetivo: recuperar DR Pharma e Inversiones Oridama en la interfaz y permitir cambiar la empresa de trabajo de forma autenticada. La empresa seleccionada se conserva en la sesión. En Inicio se muestran exclusivamente los artes, solicitudes y vigencias de dicha empresa.

INSTALACIÓN
1. Cerrar servidor uvicorn (Ctrl+C).
2. Respaldar storage fuera de Codespaces.
3. Extraer este ZIP en la carpeta donde se encuentra app.py.
4. Ejecutar python instalar_selector_empresas.py
5. Verificar python -m py_compile app.py
6. Arrancar python -m uvicorn app:app --host 0.0.0.0 --port 8000
7. Abrir /login y cambiar de empresa en el selector del menú.

SEGURIDAD / ALCANCE
- El selector solo lista las empresas asignadas al usuario en user_companies.
- La ruta POST verifica CSRF y permiso de empresa antes de cambiar de contexto.
- La búsqueda, solicitudes recientes y vigencias de Inicio se filtran en SQL por empresa seleccionada.
- El resto de los módulos conserva las comprobaciones que ya tenía; aún NO se ha realizado el ajuste integral de todos los endpoints para obedecer el contexto activo. Un usuario con acceso a dos empresas todavía puede operar módulos con filtros propios que incluyan ambas. No utilizar como aislamiento completo entre empresas sin revisar las rutas.
- Este instalador comprueba marcas del código v2. Si el código difiere, se detiene sin reemplazar archivos.
- El instalador respalda app.py, base.html y home.html antes de cambiarlos y no modifica la base de datos.
