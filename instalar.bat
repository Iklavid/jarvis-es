@echo off
chcp 65001 >nul
cd /d "%~dp0"
title J.A.R.V.I.S. - instalacion
set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
set "VENV=%LOCALAPPDATA%\Jarvis\venv"

rem Jarvis necesita Python 3.12 (algunas librerias de audio aun no existen para 3.14)
py -3.12 -c "print()" >nul 2>nul
if errorlevel 1 (
  echo Instalando Python 3.12 con winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
)
py -3.12 -c "print()" >nul 2>nul || (echo. & echo No he podido instalar Python 3.12. Instalalo desde python.org y vuelve a ejecutar este archivo. & pause & exit /b 1)

rem El entorno de Python va fuera de la carpeta del programa (cada equipo tiene el suyo)
if exist "%VENV%" (
  "%VENV%\Scripts\python.exe" -c "import sys;sys.exit(sys.version_info[:2]!=(3,12))" 2>nul || rmdir /s /q "%VENV%"
)
if not exist "%VENV%" (
  echo Creando entorno de Python en %VENV% ...
  py -3.12 -m venv "%VENV%"
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || (echo. & echo Fallo instalando dependencias. & pause & exit /b 1)
copy /y requirements.txt "%VENV%\requisitos_instalados.txt" >nul

if not exist .env (
  copy .env.ejemplo .env >nul
  echo.
  echo Se ha creado el archivo .env. Pega tu clave de DeepSeek y guarda.
  notepad .env
)
echo.
echo ==========================================
echo  Instalacion terminada.
echo  Abre Jarvis con "iniciar_jarvis.bat".
echo ==========================================
pause
