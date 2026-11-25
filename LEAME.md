# Medigray — documento obligatorio al crear un arte

## Instalación
1. Detenga Uvicorn (`Ctrl+C`). Respalde `storage` fuera de Codespaces.
2. Copie `instalar_documento_arte.py` a `/workspaces/Medigray`.
3. Ejecute `python instalar_documento_arte.py`.
4. Ejecute `python -m py_compile app.py`.
5. Reinicie Uvicorn y abra `/artes/nuevo`.

## Comportamiento
- Obliga a seleccionar uno de dos medios al registrar un nuevo arte: carga local de PDF/DOCX de hasta 20 MB, o enlace HTTPS de Google Drive/Docs.
- La carga local se guarda como adjunto del arte. El enlace de Drive se registra como vínculo en la tabla `art_document_links`, NO como respaldo descargado ni copia de seguridad.
- El detalle del arte incluye los documentos y enlaces iniciales.
- No modifica la lógica existente de `versions`, su PDF aprobado o los estados de vigencia por país; el documento de creación NO equivale a una revisión aprobada.
- Es una ampliación del prototipo; la regla de documento obligatorio aplica a nuevos artes creados desde este formulario, no a registros previos ni a otras rutas futuras. Para garantía integral necesita una regla de negocio consistente en todas las interfaces de creación y pruebas.
- Al adjuntar PDF inicial con almacenamiento Drive seleccionado, esta versión lo guarda LOCALMENTE. La carga Drive integrada sigue disponible por los adjuntos existentes, cuando esté configurada.
