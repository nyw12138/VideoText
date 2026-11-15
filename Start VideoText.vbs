Option Explicit

Dim shell, fileSystem, projectRoot, pythonw
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonw = fileSystem.BuildPath(projectRoot, ".venv\Scripts\pythonw.exe")

If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "--check" Then
        If fileSystem.FileExists(pythonw) Then
            WScript.Echo "OK: " & pythonw
            WScript.Quit 0
        End If
        WScript.Echo "ERROR: pythonw.exe was not found: " & pythonw
        WScript.Quit 1
    End If
End If

If Not fileSystem.FileExists(pythonw) Then
    MsgBox "The VideoText virtual environment was not found." & vbCrLf & pythonw, _
        vbCritical, "VideoText"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectRoot
shell.Run Chr(34) & pythonw & Chr(34) & " -m videotext.gui", 0, False
