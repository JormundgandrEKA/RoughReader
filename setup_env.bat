@echo off
rem Sets PY and PYW to a Python that is safe to build and run RoughReader with.
rem
rem Anaconda's (and Spyder's bundled) Python is not: Qt from pip cannot load inside it, and apps
rem built with it miss DLLs. So a private virtual environment (.venv) is made from a standard
rem Python 3.10+ that is already on this PC. Nothing is downloaded except pip packages.
rem Called by run/build/diagnose.bat.
set PY=
set PYW=
set BASEPY=
set PYTHONNOUSERSITE=1
set PYTHONPATH=
set PYTHONHOME=
if exist ".venv\Scripts\python.exe" goto :have_venv

call :try py -3.13
call :try py -3.12
call :try py -3.11
call :try py -3.14
call :try "%USERPROFILE%\.local\bin\python.exe"
call :try python
call :try python3
if not defined BASEPY goto :none
echo Creating the .venv environment from: %BASEPY%
%BASEPY% -m venv .venv
if errorlevel 1 goto :none
if not exist ".venv\Scripts\python.exe" goto :none

:have_venv
set PY=.venv\Scripts\python.exe
set PYW=.venv\Scripts\pythonw.exe
if not exist "%PYW%" set PYW=%PY%
rem Keep other software's DLLs (Anaconda's among them) out of reach.
set PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0
"%PY%" -m pip --version >nul 2>nul
if errorlevel 1 "%PY%" -m ensurepip --upgrade
"%PY%" -m pip --version >nul 2>nul
if errorlevel 1 goto :none
exit /b 0

:try
rem Remember the first usable base Python: 3.10+, and not Anaconda or Spyder.
if defined BASEPY exit /b 0
%* -c "import sys; t=(sys.version+sys.base_prefix+sys.executable).lower(); sys.exit(1 if sys.version_info<(3,10) or 'conda' in t or 'spyder' in t else 0)" >nul 2>nul
if errorlevel 1 exit /b 0
set BASEPY=%*
exit /b 0

:none
echo.
echo No usable Python. Install Python 3.12 or newer from python.org, then run this again.
exit /b 1
