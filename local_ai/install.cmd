@echo off
rem Moo Local for Windows. Double-click this file: it fetches the installer
rem from the Moo and runs it. The Moo filled in its own address below.
title Moo Local
set "MOO=__MOO_URL__"
echo Setting up Moo Local from the Moo at %MOO% ...
echo This takes a while the first time, about 6 GB to download.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing '%MOO%/local-ai/install.ps1' -OutFile (Join-Path $env:TEMP 'moo-local-install.ps1')"
if errorlevel 1 (
  echo.
  echo The Moo at %MOO% did not answer. Is this computer on the same wifi as the Moo?
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\moo-local-install.ps1" -Moo "%MOO%"
echo.
pause
