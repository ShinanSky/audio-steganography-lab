@echo off
call "%~dp0windows_launch.cmd" generator
set "TASK_EXIT=%ERRORLEVEL%"
if not "%TASK_EXIT%"=="0" pause
exit /b %TASK_EXIT%
