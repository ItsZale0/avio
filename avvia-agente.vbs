' Avvia Carpool Hunter in background, senza finestre di terminale.
'
' Usato dalla cartella di avvio: al login parte tutto in silenzio.
' I log si trovano in data\logs\.

Option Explicit

Dim shell, fso, base, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = base & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    pythonw = base & "\.venv\Scripts\python.exe"
End If

If Not fso.FileExists(pythonw) Then
    ' Nessun ambiente virtuale: non si puo' fare niente.
    WScript.Quit 1
End If

command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & base & "\scripts\run_agent.py" & Chr(34)

' 0 = finestra nascosta, False = non attendere la fine
shell.Run command, 0, False
