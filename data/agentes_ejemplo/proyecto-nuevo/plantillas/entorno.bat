@echo off
rem ============================================================================
rem  entorno.bat - Administración del proyecto MiAplicacion
rem  Uso con menú:  entorno.bat
rem  Uso sin menú:  entorno.bat <orden>
rem  Órdenes: crear, instalar, ejecutar, depurar, pruebas, compilar,
rem           comprobar, limpiar, datos
rem  Guardar con finales de línea CRLF y UTF-8 SIN BOM.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "NOMBRE_APP=MiAplicacion"
rem Versión de Python del proyecto (por ejemplo 3.13). Vacía = la predeterminada del
rem lanzador py (la marcada con * en "py -0p"). No se usa "py -3": elegiría la más
rem reciente instalada, quizá una preliminar sin ruedas de wxPython.
set "VERSION_PY="
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "BUILD_DIR=build\%NOMBRE_APP%"

if not "%~1"=="" goto ORDEN

:MENU
echo.
echo  %NOMBRE_APP% - Menú de administración
echo  1. Crear el entorno virtual e instalar dependencias
echo  2. Instalar o reinstalar dependencias
echo  3. Ejecutar la aplicación
echo  4. Ejecutar con consola (depuración)
echo  5. Ejecutar las pruebas
echo  6. Compilar con cx_Freeze
echo  7. Comprobar que la versión compilada arranca
echo  8. Limpiar compilaciones y cachés
echo  9. Abrir la carpeta data
echo  0. Salir
echo.
set "OPCION="
set /p "OPCION=Elija una opción y pulse Intro: "
if "%OPCION%"=="1" call :CREAR & goto PAUSA
if "%OPCION%"=="2" call :INSTALAR & goto PAUSA
if "%OPCION%"=="3" call :EJECUTAR & goto PAUSA
if "%OPCION%"=="4" call :DEPURAR & goto PAUSA
if "%OPCION%"=="5" call :PRUEBAS & goto PAUSA
if "%OPCION%"=="6" call :COMPILAR & goto PAUSA
if "%OPCION%"=="7" call :COMPROBAR & goto PAUSA
if "%OPCION%"=="8" call :LIMPIAR & goto PAUSA
if "%OPCION%"=="9" call :DATOS & goto PAUSA
if "%OPCION%"=="0" goto FIN
echo Opción no válida: %OPCION%
goto MENU

:PAUSA
echo.
pause
goto MENU

:ORDEN
if /i "%~1"=="crear" call :CREAR & goto SALIR
if /i "%~1"=="instalar" call :INSTALAR & goto SALIR
if /i "%~1"=="ejecutar" call :EJECUTAR & goto SALIR
if /i "%~1"=="depurar" call :DEPURAR & goto SALIR
if /i "%~1"=="pruebas" call :PRUEBAS & goto SALIR
if /i "%~1"=="compilar" call :COMPILAR & goto SALIR
if /i "%~1"=="comprobar" call :COMPROBAR & goto SALIR
if /i "%~1"=="limpiar" call :LIMPIAR & goto SALIR
if /i "%~1"=="datos" call :DATOS & goto SALIR
echo Orden desconocida: %~1
echo Órdenes válidas: crear, instalar, ejecutar, depurar, pruebas, compilar, comprobar, limpiar, datos
exit /b 2

:SALIR
exit /b %ERRORLEVEL%

:CREAR
if exist "%PY%" (
	echo El entorno virtual ya existe; se reutiliza.
) else (
	echo Creando el entorno virtual en %VENV%
	if defined VERSION_PY (
		py -%VERSION_PY% -m venv "%VENV%"
	) else (
		py -m venv "%VENV%"
	)
	if not exist "%PY%" (
		echo ERROR: no se pudo crear el entorno virtual. Compruebe con "py -0p" que Python %VERSION_PY% está instalado.
		exit /b 1
	)
)
"%PY%" --version
call :INSTALAR
exit /b %ERRORLEVEL%

:INSTALAR
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
	echo ERROR: falló la instalación de dependencias.
	exit /b 1
)
echo Dependencias instaladas.
exit /b 0

:EJECUTAR
call :COMPROBAR_VENV || exit /b 1
rem pythonw no abre consola: es como la verá el usuario. Los errores van a data\logs.
start "" "%PYW%" main.py
exit /b 0

:DEPURAR
call :COMPROBAR_VENV || exit /b 1
set "APP_DEPURACION=1"
"%PY%" -X dev main.py
set "CODIGO=!ERRORLEVEL!"
set "APP_DEPURACION="
echo La aplicación terminó con el código !CODIGO!.
exit /b !CODIGO!

:PRUEBAS
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pytest tests -q
if errorlevel 1 (
	echo Hay pruebas que fallan.
	exit /b 1
)
echo Todas las pruebas pasan.
exit /b 0

:COMPILAR
call :COMPROBAR_VENV || exit /b 1
"%PY%" setup_cx.py build
if errorlevel 1 (
	echo ERROR: la compilación ha fallado.
	exit /b 1
)
call :COMPROBAR
exit /b %ERRORLEVEL%

:COMPROBAR
if not exist "%BUILD_DIR%\%NOMBRE_APP%.exe" (
	echo No existe %BUILD_DIR%\%NOMBRE_APP%.exe. Compile primero.
	exit /b 1
)
rem La prueba de humo usa otra carpeta data (fuera de build\%NOMBRE_APP%) para no ensuciar la distribución.
set "APP_DATA_DIR=%~dp0build\comprobacion_data"
"%BUILD_DIR%\%NOMBRE_APP%.exe" --comprobar-arranque
set "CODIGO=!ERRORLEVEL!"
set "APP_DATA_DIR="
if not "!CODIGO!"=="0" (
	echo ERROR: la versión compilada no arrancó bien (código !CODIGO!^). Revise build\comprobacion_data\logs.
	exit /b 1
)
echo La versión compilada arranca y se cierra correctamente.
exit /b 0

:LIMPIAR
if exist build rmdir /s /q build
if exist .pytest_cache rmdir /s /q .pytest_cache
if exist .ruff_cache rmdir /s /q .ruff_cache
echo Limpieza terminada. El entorno virtual y la carpeta data no se tocan.
exit /b 0

:DATOS
if not exist data mkdir data
start "" explorer "%~dp0data"
exit /b 0

:COMPROBAR_VENV
if exist "%PY%" exit /b 0
echo El entorno virtual no existe. Use primero la opción 1 (crear).
exit /b 1

:FIN
endlocal
exit /b 0
