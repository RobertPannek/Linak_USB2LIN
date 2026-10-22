@echo off
setlocal
py -m pip install --upgrade pip
py -m pip install hidapi pyinstaller
if errorlevel 1 (
  echo Installation fehlgeschlagen. Prüfe Python und Internetverbindung.
  pause
  exit /b 1
)
echo Abhängigkeiten installiert.
pause
