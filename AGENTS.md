# Reglas permanentes del proyecto

## Versionado y respaldos

- Validar los cambios antes de publicarlos.
- Registrar cada cambio de producción en Git con un mensaje descriptivo.
- Crear una etiqueta de versión semántica para entregas relevantes.
- Crear un respaldo local recuperable del repositorio antes de cambios estructurales o migraciones.
- Respaldar Google Sheets antes de cambios masivos de datos.
- Nunca incluir claves, credenciales, archivos `.env` ni datos sensibles en Git.
- Publicar en `main` únicamente con autorización explícita del usuario.

## Verificación mínima

- Ejecutar `python3 -m py_compile app_v2.py seace_sync.py` cuando cambie código Python.
- Ejecutar `git diff --check` antes de crear una versión.
- Confirmar el despliegue visible en Streamlit después de publicar.
