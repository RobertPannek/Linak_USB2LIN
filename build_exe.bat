@echo off
cd /d "%~dp0"
py -m PyInstaller --noconfirm --clean --onefile --windowed --name LINAK_USB2LIN06 linak_gui.pyw
if errorlevel 1 (
  echo Build fehlgeschlagen.
  pause
  exit /b 1
)
echo Fertig: dist\LINAK_USB2LIN06.exe
pause
