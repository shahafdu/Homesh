' Run the storage check with no window at all.
'
' A scheduled task that launches powershell.exe pops a console over whatever you
' are doing, every few minutes, for ever. "-WindowStyle Hidden" does not prevent
' it -- the console is created and then hidden, which is a flash rather than
' nothing. Running the task under an S4U principal does prevent it, and needs
' administrator rights to register, which an ordinary start does not have.
'
' This is the remaining way: WScript.Shell.Run with a window style of 0 starts
' the process hidden from the outset. Four lines and no privileges.

Dim shell, fso, here, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Beside this file, so the pair can be moved or the repository renamed without
' anything to update.
here = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & _
          here & "\start-homesh.ps1"" -Sync"

' 0 = hidden, False = do not wait. The task's own time limit is the backstop.
shell.Run command, 0, False
