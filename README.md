# Medigray v2 — DR Pharma e Inversiones Oridama

**Prototipo para pruebas internas; no validado para uso regulatorio productivo.**

## Instalación en GitHub Codespaces (sin licencias de software)

1. Copie los archivos del ZIP a la raíz de su proyecto (`app.py`, `requirements.txt`, `templates/`, `static/`).
2. **Antes de sustituir una instalación existente**, guarde una copia de la carpeta `storage/` completa (base `artes.sqlite3`, clave de sesión y archivos). No copie encima de la base sin respaldarla.
3. En la terminal, por separado:

```bash
python -m pip install -r requirements.txt
python app.py crear-admin
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Si ya existen usuarios, `crear-admin` informa que no debe crear uno nuevo. Para **restablecer la clave** desde una terminal privada:

```bash
python app.py cambiar-clave-admin
```

4. Abra el **puerto 8000**, con visibilidad **Private**, y entre a `/login`.
5. Desde **Configuración**, elija **DR Pharma** o **Inversiones Oridama**, agregue preguntas, active opciones y configure almacenamiento.
6. Desde **Usuarios**, asigne cada nuevo usuario a una empresa. El primer administrador recibe acceso a ambas empresas; revise estos accesos con el equipo de TI antes de operar.

## Separación de empresas

- Cada arte tiene `company_id` y código SAP. El par `(company_id, master_code)` debe ser único en instalaciones nuevas.
- Pueden existir el mismo código maestro y SAP en ambas empresas, con versiones por país, solicitudes y documentos completamente distintos.
- Los usuarios nuevos se asignan a una empresa; solo ven artes de empresas autorizadas. El administrador inicial ve ambas.
- **Instalaciones previas**: SQLite conserva una restricción global `UNIQUE(master_code)` creada por la versión anterior. El sistema no elimina esta restricción automáticamente para evitar riesgos de pérdida de datos; si necesita códigos maestros idénticos en ambas empresas después de migrar, habrá que reconstruir esa tabla con una migración respaldada. Los códigos SAP iguales sí se aceptan.

## Formularios editables y módulos

- Configuración → campos adicionales: texto, párrafo, número, fecha, lista, Sí/No/N/A; visibles u obligatorios. Se guardan sus respuestas en `form_answers`.
- Configuración → módulos: interruptores por empresa. **Limitación:** la configuración es persistente y bloquea rutas principales de creación, edición y consulta de módulos de arte, solicitud, versiones y proveedores. La navegación y el bloqueo integral de todas las operaciones aún requieren pruebas y ampliaciones; no utilizar como mecanismo único de seguridad.
- Desactivar una pregunta no borra sus respuestas previas. Cambiar el nombre sí altera cómo se etiqueta su respuesta histórica en pantalla; se necesita versionado de definiciones antes de validación regulatoria.

## Archivos y Drive

- Por defecto los adjuntos se guardan en `storage/files` (sin depender de Drive). PDF, JPG, PNG, DOCX, XLSX, máximo 20 MB.
- Para Drive: `python -m pip install -r requirements-drive.txt`; configurar `GOOGLE_APPLICATION_CREDENTIALS` apuntando a un JSON de **cuenta de servicio administrada por las empresas**, con permisos al destino. No guarde JSON ni claves en Git, publique el repositorio ni use un Drive personal. En la consola, establezca una carpeta accesible, preferentemente una unidad compartida de Google Workspace.
- La integración intenta subir y descargar archivos mediante la API oficial; depende de credenciales, permisos y cuota disponibles, no garantiza gratuidad de almacenamiento corporativo.
- Cambiar el destino no mueve archivos antiguos. Los PDF principales de las revisiones existentes siguen en el almacenamiento local; la preferencia aplica a archivos del módulo **adjuntos**.

## Seguridad y límites antes de producción

- Recomendado HTTPS, copias de seguridad y restauración probada, roles por empresa más granulares, SSO/MFA, rate limiting de acceso, validación estricta de documentos, controles de integridad, pruebas de penetración y validación GxP.
- Codespaces **no es hosting empresarial permanente**. SQLite de Codespaces no debería ser repositorio definitivo compartido; migrar a PostgreSQL y hosting con almacenamiento persistente.
- Auditoría es básica, no inmutable. No equivale a un sistema validado GxP o a firmas electrónicas reguladas.
- El Excel `HC-AC-001` se usó como referencia de módulos (Solicitudes, Afectaciones, Artes, Versiones, Proveedores, Trámites, Transiciones, ArchivosDrive, etc.), **no se importaron los registros reales ni datos ficticios**.
