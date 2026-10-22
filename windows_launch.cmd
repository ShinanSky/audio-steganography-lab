@echo off
setlocal
cd /d "%~dp0"
set "TASK_MODE=%~1"
if "%TASK_MODE%"=="install" goto install
if "%TASK_MODE%"=="generator" goto launch
if "%TASK_MODE%"=="reader" goto launch
echo Unknown launch mode.
exit /b 2

:install
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>&1
if errorlevel 1 goto python_fallback
py -3 -c "import sys, tkinter, venv; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 goto missing_python
py -3 -m venv .venv
if errorlevel 1 exit /b 1
goto dependencies

:python_fallback
where python >nul 2>&1
if errorlevel 1 goto missing_python
python -c "import sys, tkinter, venv; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 exit /b 1

:dependencies
".venv\Scripts\python.exe" -c "import sys, tkinter; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 goto missing_python
".venv\Scripts\python.exe" -m pip install -r requirements.txt
exit /b %ERRORLEVEL%

:launch
if not exist ".venv\Scripts\python.exe" goto missing_environment
".venv\Scripts\python.exe" launcher.py %TASK_MODE%
exit /b %ERRORLEVEL%

:missing_environment
echo Run the dependency installer in this folder first.
exit /b 1

:missing_python
echo Install Python 3.10 or later with Tkinter and the Python launcher.
exit /b 1
