' Run the folder watcher with no window at all, as follow-storage.vbs does.
' WScript.Shell.Run with a window style of 0 starts it hidden from the outset.

Dim shell, fso, here, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & _
          here & "\watch-folders.ps1"""

shell.Run command, 0, False
