@echo off
echo Installing LiveSub (needs Python 3.11+ from python.org)...
py -m venv "%~dp0.venv"
if errorlevel 1 (echo Python not found - install it from python.org first & pause & exit /b 1)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
echo.
echo Done! Double-click run.bat to start LiveSub.
pause
