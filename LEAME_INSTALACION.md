# Medigray — Extensión consolidada de gestión (bloques 5–7)

Compatible con Medigray v2 y los Bloques 2, 3 y 4. **No implementa un sistema GxP validado, controles de acceso integrales ni firma electrónica regulada.**

## Contenido
- `medigray_final.py`: catálogo de productos SAP y proveedores por empresa, seguimiento de transiciones y pendientes, resumen de configuración.
- `mgf_catalogos.html`, `mgf_seguimiento.html`, `mgf_admin.html`: páginas.
- `instalar_mejoras_finales.py`: instalador no destructivo (hace copia de `app.py` y `templates/base.html`).
- `diagnostico_medigray.py`: revisión del despliegue y de las rutas.

## Pasos en GitHub Codespaces
1. Detenga Uvicorn mediante Ctrl+C en la terminal que lo ejecuta. Mantenga el puerto privado.
2. Haga respaldo **fuera de Codespaces** de `storage` y de su repositorio; además puede crear un respaldo local `cp -a storage /tmp/medigray_respaldo_previo` (en `/tmp` no es permanente).
3. Suba el ZIP a `/workspaces/Medigray` (carpeta donde está `app.py`). **No abra el ZIP como texto.**
4. Extraiga `unzip -oq Medigray_Mejoras_Restantes.zip`.
5. Ejecute `python instalar_mejoras_finales.py` (solo cuando estén instalados los bloques 2, 3 y 4).
6. Compruebe `python -m py_compile app.py medigray_final.py` y `python diagnostico_medigray.py`.
7. Inicie `python -m uvicorn app:app --host 0.0.0.0 --port 8000`.
8. Visite `/catalogos`, `/seguimiento`, `/administracion/resumen`.

Si aparece `[Errno 98] address already in use`, ya hay un servidor ocupando el puerto 8000. Pare su instancia anterior con Ctrl+C. Si aparece error 401 al abrir `/`, pruebe `/login` y revise que el servidor siga activo; si Codespaces usa puerto privado, compruebe su autenticación en GitHub.

## Alcance real
Esta extensión añade **catálogos**, **seguimiento en pantalla** y **resumen administrativo**. Reutiliza formularios configurables existentes del menú Configuración. No modifica flujos de aprobación ya instalados, no envía emails automáticamente, no implementa migración completa de Google Drive, ni completa todas las restricciones entre empresas/rutas heredadas. Los estados del catálogo pueden activarse y desactivarse sin borrar el historial.

## Pruebas mínimas
- Cree un producto de prueba para cada empresa con igual SAP y nombres diferentes; verifique que no se mezclen.
- Registre un proveedor por empresa.
- Compruebe que un usuario asignado a una sola empresa no vea la otra en `/catalogos`.
- Compruebe que `/administracion/resumen` devuelva 403 al rol no administrador.
- Cree una transición ficticia y compruebe su fecha en `/seguimiento`.

No almacene datos regulados reales hasta validar seguridad, migración, respaldo, autenticación y flujos.
