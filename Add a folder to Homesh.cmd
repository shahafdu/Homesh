@echo off
rem  Double-click this to add a folder to Homesh.
rem
rem  A .cmd rather than "open PowerShell and run a script": adding a folder is a
rem  one-time act somebody performs once and forgets, and making it a terminal
rem  chore is what turned a thirty-second job into a reason to give up on it.
rem
rem  ExecutionPolicy is set for this process only -- nothing about the machine is
rem  changed, and the default policy stops a double-clicked .ps1 dead otherwise.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\grant-folder.ps1" %*
if errorlevel 1 (
  echo.
  pause
)
