@echo off
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0web.py"
start "" http://localhost:7765
