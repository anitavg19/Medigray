# Control de Cambios y Versiones de Artes de Empaque

**Derefarma · Oridama — MVP demostrativo local y gratuito**

## Qué incluye

- Inicio de sesión y cinco roles: Administrador, Regulatorios, Gestión de Calidad, Compras y Vista.
- Catálogo de artes por código maestro, SAP, empresa, producto y tipo de empaque.
- Solicitudes de control de cambios con selección de ruta interna/sanitaria y justificación N/A.
- Registro de afectaciones y decisiones con segregación básica (nadie puede aprobar su propia solicitud).
- Carga de archivos PDF con SHA-256 y preservación de revisiones.
- Versiones y vigencias independientes por país; bloqueo de doble versión oficial para un arte y país.
- Confirmaciones de impresión por proveedor.
- Panel de alertas de transiciones y aprobaciones pendientes.
- Registro de auditoría de operaciones importantes.

## Instalación en Windows (gratis)

1. Instale Python 3.11 o más nuevo desde https://www.python.org/downloads/ (marque **Add Python to PATH**).
2. Descomprima el ZIP en una carpeta de su PC.
3. Abra PowerShell dentro de la carpeta `artes_control_grupo`.
4. Cree el entorno virtual y active:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   Si Windows bloquea la activación, ejecute sin activar el entorno: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
5. Cree la primera cuenta administradora:

   ```powershell
   python app.py crear-admin
   ```
   O con el Python del entorno: `.\.venv\Scripts\python.exe app.py crear-admin`.
6. Inicie el sistema:

   ```powershell
   python -m uvicorn app:app --host 127.0.0.1 --port 8000
   ```
7. Abra **http://127.0.0.1:8000/login** en su navegador.

Para detener: presione **Ctrl + C** en PowerShell.

## Sin costos de licencia

El código es Python / FastAPI / SQLite / HTML / CSS y no requiere una plataforma comercial. Localmente solo necesita su computadora. Para usuarios en distintas oficinas, necesitará un servidor accesible o red privada; el alojamiento público y dominio pueden generar costos.

## Carpetas

- `app.py`: aplicación, reglas, rutas, autenticación y modelo de datos.
- `templates/`: pantallas web editables.
- `storage/`: se crea automáticamente; contiene la base de datos, PDFs y secreto de sesión. **No compartir ni subir a repositorios públicos.**

## Límites importantes — no usar aún como sistema GxP oficial

Este es un MVP **para pruebas internas**, no una aplicación farmacéutica validada. Antes de almacenar datos productivos o convertirla en registro oficial de aprobaciones, es necesario:

1. Validar requisitos, flujos de trabajo, matriz de impacto regulatorio y controles conforme a las políticas de ambas empresas.
2. Implementar SSO/MFA, recuperación y cambio de contraseña, administración de sesiones, límites de intentos de acceso, HTTPS, respaldo verificado y monitoreo.
3. Migrar SQLite a PostgreSQL para uso multiusuario relevante y asegurar transacciones concurrentes y restricciones de integridad sólidas.
4. Asegurar audit trail independiente y resistente a manipulación por administradores, preservación legal, retención y restauración; agregar evidencia verificable de resoluciones ministeriales y firmas electrónicas si aplican.
5. Implementar permisos por empresa y país (el MVP **solo** restringe por rol), y autorización fina de datos y documentos.
6. Validar PDF con antimalware y reglas más estrictas de almacenamiento; proteger secretos y archivos contra acceso directo.
7. Añadir estados y fechas de vigencia completos por jurisdicción, con validaciones de no solapamiento y autorización previa; el MVP implementa únicamente restricciones básicas.
8. Formalizar pruebas, revisión de seguridad, copias de seguridad, control de cambios del propio software y procedimientos de despliegue.

**Limitación funcional específica:** registrar una aprobación de flujo tipo “Sanitaria” en este prototipo no verifica un acto oficial real ni equivale a una autorización ministerial. La clasificación también debe ser revisada por Regulatorios.

## Futuras mejoras

- Reportes y exportación Excel/CSV.
- Envío de avisos por correo y tareas programadas.
- Identidad corporativa, derechos por empresa/país y cambio de contraseña.
- Integración oficial SAP.
- Comparador visual PDF por revisión.
- Solicitudes que afectan varios países y múltiples artes, con aprobaciones por jurisdicción.
