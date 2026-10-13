# Compila ClaudeUsage.exe y el instalador ClaudeUsage-Setup.exe en .\dist
# Requisitos: Python 3.10+ en el PATH.  Uso: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
# ruta corta: venv y PyInstaller fallan con rutas temporales muy largas
$work = Join-Path ([System.IO.Path]::GetFullPath("$env:LOCALAPPDATA\Temp")) 'cu-build'
New-Item -ItemType Directory -Force $work | Out-Null

if (-not (Test-Path "$work\env\Scripts\pyinstaller.exe")) {
    python -m venv "$work\env"
    & "$work\env\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check pyinstaller
}
$pyi = "$work\env\Scripts\pyinstaller.exe"

# icono (data\icon.ico / icon.png), generado por la propia app
python "$root\ClaudeUsage.pyw" --make-icons
$common = @('--noconfirm', '--onefile', '--windowed', '--icon', "$root\data\icon.ico",
            '--distpath', "$root\dist", '--workpath', "$work\work", '--specpath', $work)

& $pyi @common --name ClaudeUsage --version-file "$root\packaging\version_app.txt" "$root\ClaudeUsage.pyw"
& $pyi @common --name ClaudeUsage-Setup --version-file "$root\packaging\version_setup.txt" `
    --add-data "$root\dist\ClaudeUsage.exe;." --add-data "$root\data\icon.png;." "$root\setup_installer.py"

Write-Host "Listo: $root\dist\ClaudeUsage-Setup.exe"
