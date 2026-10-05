@echo off
rem Run RoughReader from source.
setlocal
cd /d "%~dp0"
call "%~dp0setup_env.bat"
if errorlevel 1 goto :fail
"%PY%" -c "import PySide6.QtCore, PIL" >nul 2>nul
if errorlevel 1 "%PY%" -m pip install --no-warn-script-location -r requirements.txt
if errorlevel 1 goto :fail
start "" "%PYW%" run_roughreader.py %*
exit /b 0
:fail
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
