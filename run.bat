@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" -m musicplayer %*
