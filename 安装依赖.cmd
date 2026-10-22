@echo off
call "%~dp0windows_launch.cmd" install
set "TASK_EXIT=%ERRORLEVEL%"
pause
exit /b %TASK_EXIT%
