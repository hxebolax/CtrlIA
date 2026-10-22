@echo off
rem ============================================================================
rem  subir_github.bat - Sube a GitHub los cambios del código de Ctrl+IA
rem  (remoto origin, rama main). tools\subir_github.py hace todo el trabajo.
rem
rem  Con doble clic: commit con un mensaje automático con la fecha y el número
rem  de archivos cambiados, y espera a que pulse una tecla al final.
rem  Con mensaje (no espera al final):
rem    subir_github.bat Arreglo del menú Ver
rem    subir_github.bat --ensayo      dice lo que haría sin cambiar nada
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
".venv\Scripts\python.exe" tools\subir_github.py %*
set "CODIGO=%ERRORLEVEL%"
if "%~1"=="" (
	echo.
	pause
)
exit /b %CODIGO%
