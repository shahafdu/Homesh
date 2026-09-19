@echo off
rem  Double-click this for a one-time code that signs you in to the standby.
rem
rem  The standby starts with no passkeys -- a passkey belongs to an address, and
rem  it has a different one -- so the first sign-in on each phone needs a code.
rem  This asks the standby for one over Tailscale and shows it in this window,
rem  and nowhere else. Held open, because the code has to be read off it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\standby-signin.ps1" %*
echo.
pause
