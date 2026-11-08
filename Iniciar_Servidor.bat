@echo off
chcp 65001 > nul
title Mini CRM Móvil & Preventa - Servidor Local

echo ===================================================
echo     MINI CRM MÓVIL ^& PREVENTA (MODO LOCAL)
echo ===================================================
echo.
echo Iniciando base de datos SQLite y servidor FastAPI...
echo.

set PYTHON_CMD=python
if exist "..\sistema_gestion\.venv\Scripts\python.exe" (
    set PYTHON_CMD=..\sistema_gestion\.venv\Scripts\python.exe
)

%PYTHON_CMD% -c "import uvicorn" 2>nul
if %errorlevel% neq 0 (
    echo [!] Instalando dependencias necesarias...
    %PYTHON_CMD% -m pip install fastapi uvicorn qrcode[pil] jinja2
)

echo [*] Servidor ejecutándose en:
echo     Local:    http://localhost:8000
echo.
echo Para abrir desde celulares en la misma red Wi-Fi,
echo abrí el navegador y hacé clic en "QR Wi-Fi".
echo.
echo Presioná Ctrl + C para detener el servidor.
echo ===================================================
echo.

%PYTHON_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
