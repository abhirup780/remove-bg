# Builds dist\BG Remove\ (PyInstaller) and installer\BG-Remove-Setup-<ver>.exe (Inno Setup).
# Needs: .venv with requirements.txt + pyinstaller, models\*.onnx, Inno Setup 6,
#        redist\MicrosoftEdgeWebview2Setup.exe (https://go.microsoft.com/fwlink/p/?LinkId=2124703)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

& .\.venv\Scripts\python.exe -m PyInstaller bgremove.spec --noconfirm
if ($LASTEXITCODE) { throw 'PyInstaller failed' }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
& $iscc /Q installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }

Get-ChildItem installer\*.exe | ForEach-Object { '{0}  {1:N0} MB' -f $_.FullName, ($_.Length / 1MB) }
