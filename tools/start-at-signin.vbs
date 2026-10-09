' Start Homesh when you sign in to Windows, with no window at all.
'
' Docker Desktop has a sign-in entry of its own, but that is a switch in Task
' Manager's Startup apps that a clean-up tool can turn off without a word -- and
' one was: after a restart the engine never came up, so neither did Homesh.
' Docker alone was never the whole job anyway; start-homesh.ps1 also re-attaches
' the external drive, which has to happen on every boot.
'
' Hidden through WScript for the reason given in follow-storage.vbs. Output goes
' to a log, because a start that fails unattended should leave something to read.

Dim shell, fso, here, logDir, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
logDir = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Homesh"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

command = "cmd.exe /c powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & _
          here & "\start-homesh.ps1"" > """ & logDir & "\start-at-signin.log"" 2>&1"

shell.Run command, 0, False
