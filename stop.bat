@echo off
if exist "%~dp0livesub.pid" (
    for /f %%p in ('type "%~dp0livesub.pid"') do taskkill /f /pid %%p
    del "%~dp0livesub.pid"
) else (
    taskkill /f /im pythonw.exe
)
