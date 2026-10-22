@echo off
rem ============================================================================
rem  publicar.bat - Publica la versión de Ctrl+IA en el servidor de
rem  actualizaciones y en GitHub (tools\publicar.py hace todo el trabajo).
rem
rem  Con doble clic: publica de verdad, pregunta antes de empezar y espera a que
rem  pulse una tecla al final para que pueda leer el resultado.
rem  Con opciones (no espera al final):
rem    publicar.bat --ensayo          dice lo que haría sin copiar, subir ni crear nada
rem    publicar.bat --sin-pruebas     no pasa las pruebas
rem    publicar.bat --sin-compilar    usa el ZIP y version.json que ya hay en build
rem    publicar.bat --reemplazar      vuelve a subir los archivos de una versión ya publicada
rem    publicar.bat --help            todas las opciones
rem
rem  Guardar con finales de línea CRLF y codificación UTF-8 SIN BOM.
rem ============================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
	echo ERROR: no existe el entorno virtual .venv. Créelo primero con: entorno.bat crear
	if "%~1"=="" pause
	exit /b 1
)
".venv\Scripts\python.exe" tools\publicar.py %*
set "CODIGO=%ERRORLEVEL%"
if "%~1"=="" (
	echo.
	pause
)
exit /b %CODIGO%
