@echo off
rem Build the portable app: one file, ..\RoughRider-Portable\RoughReader.exe, then self-test it.
rem (For a folder build instead:  python tools\build_exe.py  ->  dist\RoughReader\)
setlocal
cd /d "%~dp0"
call "%~dp0setup_env.bat"
if errorlevel 1 goto :fail
"%PY%" -m pip install --no-warn-script-location -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
"%PY%" tools\build_exe.py --onefile --out "%~dp0..\RoughRider-Portable"
if errorlevel 1 goto :fail
echo.
pause
exit /b 0
:fail
echo.
echo Build did not finish cleanly. See the messages above.
pause
exit /b 1
