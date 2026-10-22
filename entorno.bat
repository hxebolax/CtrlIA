@echo off
rem ============================================================================
rem  entorno.bat - Administración del proyecto Ctrl+IA
rem  Crea el entorno virtual, instala dependencias, ejecuta desde código,
rem  pasa las pruebas, compila con cx_Freeze (con los runtimes de Visual C++),
rem  publica versiones y sube el código a GitHub.
rem
rem  Uso con menú:        entorno.bat
rem  Uso sin menú:        entorno.bat <orden> [opciones]
rem  Órdenes: crear, instalar, actualizar, ejecutar, depurar, pruebas,
rem           compilar, compilar-debug, empaquetar, limpiar, datos,
rem           registros, diagnostico, congelar, comprobar,
rem           publicar [opciones de tools\publicar.py, por ejemplo --ensayo],
rem           subir-github [mensaje del commit o --ensayo]
rem
rem  PLANTILLA del agente «entorno-virtual». Guardar con finales de línea CRLF
rem  y codificación UTF-8 SIN BOM.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

rem ── Configuración ──────────────────────────────────────────────────────────
set "NOMBRE_APP=CtrlIA"
rem Versión de Python del proyecto (la de requires-python o .python-version).
rem Vacía = la más reciente instalada que ofrezca el lanzador py.
set "VERSION_PY=3.15"
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "SETUP=setup_cx.py"
set "BUILD_DIR=build\%NOMBRE_APP%"
rem ──────────────────────────────────────────────────────────────────────────

if not "%~1"=="" goto ORDEN

:MENU
echo.
echo  %NOMBRE_APP% - Menú de administración
echo  ==============================================
echo  1. Crear o reparar el entorno virtual e instalar dependencias
echo  2. Instalar dependencias (requirements)
echo  3. Actualizar dependencias a la última versión permitida
echo  4. Ejecutar la aplicación desde el código
echo  5. Ejecutar desde el código con consola (depuración)
echo  6. Ejecutar las pruebas
echo  7. Compilar con cx_Freeze y empaquetar en ZIP
echo  8. Compilar en modo depuración (con consola)
echo  9. Limpiar compilaciones y cachés
echo  10. Abrir la carpeta data
echo  11. Abrir la carpeta de registros
echo  12. Diagnóstico del entorno
echo  13. Guardar las versiones instaladas (requirements-lock.txt)
echo  14. Comprobar que la versión compilada arranca
echo  15. Publicar la versión en el servidor de actualizaciones y en GitHub
echo  16. Ensayo de la publicación (dice lo que haría, no publica nada)
echo  17. Subir los cambios del código a GitHub
echo  0. Salir
echo.
set "OPCION="
set /p "OPCION=Elige una opción y pulsa Intro: "
if "%OPCION%"=="1" call :CREAR & goto PAUSA
if "%OPCION%"=="2" call :INSTALAR & goto PAUSA
if "%OPCION%"=="3" call :ACTUALIZAR & goto PAUSA
if "%OPCION%"=="4" call :EJECUTAR & goto PAUSA
if "%OPCION%"=="5" call :DEPURAR & goto PAUSA
if "%OPCION%"=="6" call :PRUEBAS & goto PAUSA
if "%OPCION%"=="7" call :COMPILAR & goto PAUSA
if "%OPCION%"=="8" call :COMPILAR_DEBUG & goto PAUSA
if "%OPCION%"=="9" call :LIMPIAR & goto PAUSA
if "%OPCION%"=="10" call :DATOS & goto PAUSA
if "%OPCION%"=="11" call :REGISTROS & goto PAUSA
if "%OPCION%"=="12" call :DIAGNOSTICO & goto PAUSA
if "%OPCION%"=="13" call :CONGELAR & goto PAUSA
if "%OPCION%"=="14" call :COMPROBAR & goto PAUSA
if "%OPCION%"=="15" call :PUBLICAR & goto PAUSA
if "%OPCION%"=="16" call :PUBLICAR --ensayo & goto PAUSA
if "%OPCION%"=="17" goto MENU_SUBIR
if "%OPCION%"=="0" goto FIN
echo Opción no válida: %OPCION%
goto MENU

:MENU_SUBIR
echo.
set "MENSAJE="
set /p "MENSAJE=Mensaje del commit (o solo Intro para uno automático con la fecha): "
if defined MENSAJE (
	call :SUBIR_GITHUB "!MENSAJE!"
) else (
	call :SUBIR_GITHUB
)
goto PAUSA

:PAUSA
echo.
pause
goto MENU

:ORDEN
set "ORDEN=%~1"
call :RESTO_ARGUMENTOS %*
if /i "%ORDEN%"=="publicar" call :PUBLICAR !ARGUMENTOS! & goto SALIR
if /i "%ORDEN%"=="subir-github" call :SUBIR_GITHUB !ARGUMENTOS! & goto SALIR
if /i "%ORDEN%"=="crear" call :CREAR & goto SALIR
if /i "%ORDEN%"=="instalar" call :INSTALAR & goto SALIR
if /i "%ORDEN%"=="actualizar" call :ACTUALIZAR & goto SALIR
if /i "%ORDEN%"=="ejecutar" call :EJECUTAR & goto SALIR
if /i "%ORDEN%"=="depurar" call :DEPURAR & goto SALIR
if /i "%ORDEN%"=="pruebas" call :PRUEBAS & goto SALIR
if /i "%ORDEN%"=="compilar" call :COMPILAR & goto SALIR
if /i "%ORDEN%"=="compilar-debug" call :COMPILAR_DEBUG & goto SALIR
if /i "%ORDEN%"=="empaquetar" call :EMPAQUETAR & goto SALIR
if /i "%ORDEN%"=="limpiar" call :LIMPIAR & goto SALIR
if /i "%ORDEN%"=="datos" call :DATOS & goto SALIR
if /i "%ORDEN%"=="registros" call :REGISTROS & goto SALIR
if /i "%ORDEN%"=="diagnostico" call :DIAGNOSTICO & goto SALIR
if /i "%ORDEN%"=="congelar" call :CONGELAR & goto SALIR
if /i "%ORDEN%"=="comprobar" call :COMPROBAR & goto SALIR
echo Orden desconocida: %ORDEN%
echo Órdenes válidas: crear, instalar, actualizar, ejecutar, depurar, pruebas, compilar,
echo compilar-debug, empaquetar, limpiar, datos, registros, diagnostico, congelar, comprobar,
echo publicar, subir-github
exit /b 2

:SALIR
exit /b %ERRORLEVEL%

rem ============================================================================
rem  Subrutinas. Cada una termina con "exit /b <código>" para que el modo sin
rem  menú devuelva un código de salida útil (0 = correcto).
rem ============================================================================

:CREAR
echo.
echo === Creando el entorno virtual en %VENV% ===
if exist "%PY%" (
	echo El entorno virtual ya existe; se reutiliza.
) else (
	where py >nul 2>nul
	if not errorlevel 1 (
		if defined VERSION_PY (
			py -%VERSION_PY% -m venv "%VENV%"
			if errorlevel 1 (
				echo ERROR: no está instalado Python %VERSION_PY%. Instálelo con: py install %VERSION_PY%
				echo o desde python.org, y vuelve a intentarlo.
				exit /b 1
			)
		) else (
			rem Versión predeterminada del lanzador (la marcada con * en "py -0p"). No se usa
			rem "py -3": elegiría la más alta instalada, aunque sea una versión de prueba
			rem sin ruedas de wxPython.
			py -m venv "%VENV%"
		)
	) else (
		where python >nul 2>nul
		if errorlevel 1 (
			echo ERROR: no se encontró Python. Instala Python de 64 bits desde python.org
			echo marcando la opción de añadirlo al PATH, y vuelve a intentarlo.
			exit /b 1
		)
		python -m venv "%VENV%"
	)
)
if not exist "%PY%" (
	echo ERROR: no se pudo crear el entorno virtual.
	exit /b 1
)
"%PY%" --version
call :INSTALAR
exit /b %ERRORLEVEL%

:INSTALAR
echo.
echo === Instalando dependencias ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pip install --upgrade pip setuptools wheel
if exist requirements.txt (
	"%PY%" -m pip install -r requirements.txt
	if errorlevel 1 (
		echo ERROR: falló la instalación de requirements.txt.
		exit /b 1
	)
)
if exist requirements-dev.txt (
	"%PY%" -m pip install -r requirements-dev.txt
	if errorlevel 1 (
		echo ERROR: falló la instalación de requirements-dev.txt.
		exit /b 1
	)
)
if exist tools\despues_de_instalar.py "%PY%" tools\despues_de_instalar.py
echo Dependencias instaladas correctamente.
exit /b 0

:ACTUALIZAR
echo.
echo === Actualizando dependencias ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pip install --upgrade pip
if exist requirements.txt "%PY%" -m pip install --upgrade -r requirements.txt || exit /b 1
if exist requirements-dev.txt "%PY%" -m pip install --upgrade -r requirements-dev.txt || exit /b 1
echo Dependencias actualizadas.
exit /b 0

:EJECUTAR
echo.
echo === Ejecutando %NOMBRE_APP% desde el código ===
call :COMPROBAR_VENV || exit /b 1
rem pythonw no abre consola: es como la verá el usuario final. Los errores
rem quedan en data\logs (el lector de pantalla no se distrae con una consola).
start "" "%PYW%" main.py
exit /b 0

:DEPURAR
echo.
echo === Ejecutando %NOMBRE_APP% con consola (depuración) ===
call :COMPROBAR_VENV || exit /b 1
rem APP_DEPURACION=1 lo puede leer el módulo de registro del proyecto
rem para subir el nivel a DEBUG y mostrar también los mensajes en esta consola.
set "APP_DEPURACION=1"
"%PY%" -X dev -X faulthandler main.py
set "CODIGO=!ERRORLEVEL!"
set "APP_DEPURACION="
echo La aplicación terminó con el código !CODIGO!.
exit /b !CODIGO!

:PRUEBAS
echo.
echo === Ejecutando las pruebas ===
call :COMPROBAR_VENV || exit /b 1
if not exist tests (
	echo No hay carpeta tests.
	exit /b 0
)
"%PY%" -m pytest tests -q
if errorlevel 1 (
	echo Hay pruebas que fallan.
	exit /b 1
)
echo Todas las pruebas pasan.
exit /b 0

:COMPILAR
echo.
echo === Compilando %NOMBRE_APP% con cx_Freeze ===
call :COMPROBAR_VENV || exit /b 1
if not exist "%SETUP%" (
	echo ERROR: no existe %SETUP% en la raíz del proyecto.
	exit /b 1
)
if exist tools\antes_de_compilar.py (
	"%PY%" tools\antes_de_compilar.py || exit /b 1
)
"%PY%" "%SETUP%" build
if errorlevel 1 (
	echo ERROR: la compilación ha fallado. Revisa los mensajes anteriores.
	exit /b 1
)
if not exist "%BUILD_DIR%" (
	echo ERROR: no se generó la carpeta %BUILD_DIR%.
	exit /b 1
)
call :EMPAQUETAR
exit /b %ERRORLEVEL%

:COMPILAR_DEBUG
echo.
echo === Compilando %NOMBRE_APP% en modo depuración (con consola) ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" "%SETUP%" build --debug
if errorlevel 1 (
	echo ERROR: la compilación ha fallado.
	exit /b 1
)
echo Compilación de depuración en %BUILD_DIR%. No la distribuya: muestra consola.
exit /b 0

:EMPAQUETAR
echo.
echo === Empaquetando la distribución (ZIP y version.json) ===
if not exist tools\empaquetar.py (
	echo No existe tools\empaquetar.py; la distribución queda en %BUILD_DIR%.
	exit /b 0
)
"%PY%" tools\empaquetar.py
if errorlevel 1 (
	echo ERROR: no se pudo empaquetar la distribución.
	exit /b 1
)
exit /b 0

:PUBLICAR
echo.
echo === Publicando la versión (servidor de actualizaciones y GitHub) ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" tools\publicar.py %*
if errorlevel 1 (
	echo La publicación no se ha completado. Revisa los mensajes anteriores.
	exit /b 1
)
exit /b 0

:SUBIR_GITHUB
echo.
echo === Subiendo los cambios del código a GitHub ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" tools\subir_github.py %*
if errorlevel 1 (
	echo No se han subido los cambios. Revisa los mensajes anteriores.
	exit /b 1
)
exit /b 0

:RESTO_ARGUMENTOS
rem Deja en ARGUMENTOS todos los argumentos menos el primero (la orden).
set "ARGUMENTOS="
shift
:RESTO_BUCLE
if "%~1"=="" exit /b 0
set "ARGUMENTOS=!ARGUMENTOS! %1"
shift
goto RESTO_BUCLE

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
	echo ERROR: la versión compilada no arrancó bien (código !CODIGO!^). Revisa build\comprobacion_data\logs.
	exit /b 1
)
echo La versión compilada arranca y se cierra correctamente.
exit /b 0

:LIMPIAR
echo.
echo === Limpiando compilaciones y cachés ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist .pytest_cache rmdir /s /q .pytest_cache
if exist .ruff_cache rmdir /s /q .ruff_cache
rem Borra los __pycache__ del proyecto, pero no los del entorno virtual.
for /d /r . %%D in (__pycache__) do if exist "%%D" (
	set "RUTA=%%D"
	if "!RUTA:\.venv\=!"=="!RUTA!" if "!RUTA:\venv\=!"=="!RUTA!" rmdir /s /q "%%D"
)
echo Limpieza terminada. El entorno virtual y la carpeta data no se tocan.
exit /b 0

:DATOS
if not exist data mkdir data
start "" explorer "%~dp0data"
exit /b 0

:REGISTROS
if not exist data\logs mkdir data\logs
start "" explorer "%~dp0data\logs"
exit /b 0

:DIAGNOSTICO
echo.
echo === Diagnóstico del entorno ===
where py >nul 2>nul && py -0p
if exist "%PY%" (
	echo Python del entorno virtual:
	"%PY%" -c "import sys, platform; print(sys.version); print(platform.platform())"
	"%PY%" -c "import wx; print('wxPython', wx.version())" 2>nul || echo wxPython no está instalado.
	"%PY%" -c "import cx_Freeze; print('cx_Freeze', cx_Freeze.__version__)" 2>nul || echo cx_Freeze no está instalado.
	"%PY%" -m pip check
) else (
	echo El entorno virtual no existe todavía. Usa la opción 1.
)
tasklist /fi "imagename eq nvda.exe" 2>nul | find /i "nvda.exe" >nul && echo NVDA está en ejecución. || echo NVDA no está en ejecución.
tasklist /fi "imagename eq jfw.exe" 2>nul | find /i "jfw.exe" >nul && echo JAWS está en ejecución. || echo JAWS no está en ejecución.
exit /b 0

:CONGELAR
echo.
echo === Guardando las versiones exactas instaladas ===
call :COMPROBAR_VENV || exit /b 1
"%PY%" -m pip freeze > requirements-lock.txt
echo Versiones guardadas en requirements-lock.txt.
exit /b 0

:COMPROBAR_VENV
if exist "%PY%" exit /b 0
echo El entorno virtual no existe. Ejecuta primero la opción 1 (crear).
exit /b 1

:FIN
endlocal
exit /b 0
