$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $Pythonw -PathType Leaf)) {
    throw "Pythonw was not found at $Pythonw. Create the virtual environment and install the project first."
}

$Desktop = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($Desktop)) {
    throw 'The current user desktop directory could not be determined.'
}

$ChineseName = [string]::Concat([char[]](0x89C6, 0x9891, 0x8F6C, 0x6587, 0x5B57))
$ShortcutPath = Join-Path $Desktop "VideoText $ChineseName.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = '-m videotext.gui'
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = 'Paste a video URL and transcribe it to text'
$Shortcut.Save()

Write-Output $ShortcutPath
