"""Diagnóstico de solo lectura de las rutas (importar app puede inicializar tablas previstas)."""
from app import app,conn
required=['/login','/artes/vigentes','/artes/historial','/solicitudes','/compras','/proveedores-control','/activacion-versiones','/biblioteca-documental','/catalogos','/seguimiento','/administracion/resumen']
paths={r.path for r in app.routes}
for p in required:
    print(('OK  ' if p in paths else 'REVISAR  ')+p)
with conn() as c:
    print('EMPRESAS:')
    for company in c.execute('SELECT name FROM companies ORDER BY name'):print('  -',company[0])
    print('USUARIOS:', c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
print('Diagnóstico completado; compruebe además el acceso desde navegador y con ambos roles.')
