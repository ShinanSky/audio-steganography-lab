@echo off
call "%~dp0windows_launch.cmd" reader
set "TASK_EXIT=%ERRORLEVEL%"
if not "%TASK_EXIT%"=="0" pause
exit /b %TASK_EXIT%
