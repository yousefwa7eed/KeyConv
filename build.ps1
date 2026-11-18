# build.ps1 — يبني dist\KeyConv.exe من المصدر بأمر واحد:
#
#   powershell -ExecutionPolicy Bypass -File .\build.ps1
#
# يتطلب: Python 3.10+ مع pynput و Pillow و PyInstaller.
#   python -m pip install -r requirements.txt pillow pyinstaller

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Generating icon..."
python tools\make_icon.py

Write-Host "==> Building KeyConv.exe..."
python -m PyInstaller --noconfirm --clean keyconv.spec

Write-Host ""
Write-Host "Done: $PSScriptRoot\dist\KeyConv.exe"
Write-Host "Settings/log/PID live in: $env:LOCALAPPDATA\KeyConv"
