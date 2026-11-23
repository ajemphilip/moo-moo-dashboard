@echo off
rem Moo Local for Windows. Double-click this file: it fetches the installer
rem from the Moo and runs it. The Moo filled in its own address below.
title Moo Local installer
set "MOO=__MOO_URL__"
echo This window is the Moo Local installer from your Moo. Nothing has to be typed.
echo Fetching the installer from the Moo at %MOO% ...
echo It takes a while the first time: about 2 GB of programs. The AI models are chosen later on the Moo's AI engine screen.
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
