@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\Jarvis\venv"
if not exist "%VENV%\Scripts\python.exe" (call instalar.bat)
fc /b requirements.txt "%VENV%\requisitos_instalados.txt" >nul 2>nul || (
  echo Actualizando componentes de Jarvis...
  "%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt && copy /y requirements.txt "%VENV%\requisitos_instalados.txt" >nul
)
"%VENV%\Scripts\python.exe" interfaz.py
pause
