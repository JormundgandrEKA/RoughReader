@echo off
rem Reports for troubleshooting: diagnose*.txt here, and self-test logs with screenshots
rem in the selftest and selftest-exe folders.
setlocal
cd /d "%~dp0"
if exist dist\RoughReader\RoughReader.exe (
    echo Checking the built app...
    dist\RoughReader\RoughReader.exe --diagnose
    dist\RoughReader\RoughReader.exe --selftest selftest-exe
)
call "%~dp0setup_env.bat"
if errorlevel 1 goto :done
echo Checking the source version...
"%PY%" -m roughreader --diagnose >nul
"%PY%" -m roughreader --selftest selftest
:done
echo.
echo Reports: diagnose.txt, diagnose-exe.txt, selftest\selftest.txt, selftest-exe\selftest.txt
pause
