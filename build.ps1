# Compila TokenPace.exe y su instalador en .\dist
# Requisitos: Node 18+, Rust (rustup, toolchain MSVC) y Visual Studio Build Tools con C++.
# Uso: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

if (-not (Test-Path "$root\node_modules")) { npm ci }
npx tauri build --no-bundle
if ($LASTEXITCODE -ne 0) { throw "La compilación ha fallado" }

$dist = "$root\dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$exe = "$root\src-tauri\target\release\TokenPace.exe"
# el mismo ejecutable es la app y su instalador (si su nombre contiene "setup")
Copy-Item $exe "$dist\TokenPace.exe" -Force
Copy-Item $exe "$dist\TokenPace-Setup.exe" -Force
# las versiones 2.x ("Claude Usage") buscan este nombre para actualizarse solas
Copy-Item $exe "$dist\ClaudeUsage-Setup.exe" -Force

Get-ChildItem $dist\*.exe | Select-Object Name, @{n='MB'; e={[math]::Round($_.Length / 1MB, 1)}}
Write-Host "Listo: $dist\TokenPace-Setup.exe"
