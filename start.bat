@echo off
REM ── Contabilidad Chile ── Inicio en Windows ──────────────────────────────────
REM Ejecutar este archivo con doble clic o desde cmd.

cd /d "%~dp0"

REM Cargar variables de entorno desde .env si existe
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

REM Verificar que Python esté instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instala Python desde https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Instalar dependencias si no están
pip show flask >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias...
    pip install -r requirements.txt
)

REM Iniciar servidor
echo.
echo ============================================
echo  Contabilidad Chile
echo  Abre tu navegador en: http://localhost:5000
echo  Para acceso en red:   http://%COMPUTERNAME%:5000
echo  Presiona Ctrl+C para detener
echo ============================================
echo.

python -m flask run --host 0.0.0.0 --port 5000
pause
