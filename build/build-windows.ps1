# Gera o instalador do Bob Music para Windows.
#   Requisitos: .venv com requirements + pyinstaller, Inno Setup 6, e
#   build\vendor\ffmpeg.exe (opcional; sem ele o app baixa o ffmpeg na 1ª vez).
# Uso (na pasta do projeto):  powershell -ExecutionPolicy Bypass -File build\build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
.venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath build\dist --workpath build\work build\bobmusic.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 não encontrado (winget install JRSoftware.InnoSetup)" }

& $iscc build\windows-installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }
Get-ChildItem build\installer
