@echo off
cd /d "%~dp0"
pyw linak_gui.pyw
if errorlevel 1 py linak_gui.pyw
