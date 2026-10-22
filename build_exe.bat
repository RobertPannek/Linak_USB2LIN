@echo off
cd /d "%~dp0"
py -m PyInstaller --noconfirm --clean --onefile --windowed --name Tischsteuerung tischsteuerung.pyw
if errorlevel 1 (
  echo Build fehlgeschlagen.
  pause
  exit /b 1
)
echo Fertig: dist\Tischsteuerung.exe
pause
