@echo off
rem  Double-click this to start Homesh.
rem
rem  A .cmd rather than "open PowerShell and run a script": starting the server
rem  is the most ordinary thing anybody does with this repository, and making it
rem  a terminal chore is what turns a thirty-second job into one nobody wants to
rem  do. The same reasoning as "Add a folder to Homesh.cmd" beside it.
rem
rem  ExecutionPolicy is set for this process only. Nothing about the machine is
rem  changed, and the default policy stops a double-clicked .ps1 dead otherwise.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-homesh.ps1" %*

rem  Held open only when something went wrong, so a normal start does not leave
rem  a window demanding a keypress -- and a failure does not vanish before it
rem  can be read.
if errorlevel 1 (
  echo.
  pause
)
