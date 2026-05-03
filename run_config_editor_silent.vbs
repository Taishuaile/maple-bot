Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd /c cd /d """ & base & """ && pythonw config_editor.py"
CreateObject("WScript.Shell").Run cmd, 0, False
