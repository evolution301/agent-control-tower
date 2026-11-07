@echo off
rem Windows entry point: this machine's execution policy is Restricted, so a bare .ps1 is blocked.
rem Usage: scripts\release.cmd -Version 0.7.4
rem        scripts\release.cmd -BadgesOnly
rem        scripts\release.cmd -Version 0.7.4 -DryRun
setlocal
where pwsh >nul 2>nul
if %errorlevel%==0 goto :pwsh7
goto :ps51

:pwsh7
pwsh -NoProfile -File "%~dp0release.ps1" %*
exit /b %errorlevel%

:ps51
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1" %*
exit /b %errorlevel%
