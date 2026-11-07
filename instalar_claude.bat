@echo off
chcp 65001 >nul
title Instalar Claude Code
echo Instalando Claude Code (instalador oficial de Anthropic)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
if not exist "%USERPROFILE%\.local\bin\claude.exe" (
  echo.
  echo No se ha podido instalar. Prueba con: winget install Anthropic.ClaudeCode
  pause & exit /b 1
)
echo.
echo Ahora se abrira Claude Code para iniciar sesion con tu cuenta de Claude.
echo Cuando termines, escribe /exit y ya podras usarlo desde Jarvis.
pause
cd /d "%USERPROFILE%\Proyectos" 2>nul
"%USERPROFILE%\.local\bin\claude.exe"
