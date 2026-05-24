$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean WhatsAppBackupReader.spec

Write-Host ""
Write-Host "Build completed."
Write-Host "Executable: dist\\WhatsAppBackupReader.exe"
