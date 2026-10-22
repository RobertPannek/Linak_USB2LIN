@echo off
cd /d "%~dp0"
pyw tischsteuerung.pyw
if errorlevel 1 py tischsteuerung.pyw
