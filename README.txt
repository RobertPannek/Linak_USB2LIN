LINAK USB2LIN06 Windows GUI (experimentell)
=============================================

Zielgerät
---------
USB2LIN06: USB\VID_12D3&PID_0002
LINAK CBD6S

Diese erste Windows-GUI verwendet HID Feature Reports nach dem öffentlich verfügbaren
Projekt gryf/linak-ctrl als Protokollreferenz. Sie wurde nicht am physischen Gerät getestet.
Sie ist eine experimentelle Ausgangsbasis, keine zertifizierte Steuerung.

Dateien
-------
- linak_gui.pyw: Tkinter-GUI mit Verbinden, Status, Hoch/Runter (gedrückt halten), Diagnose/Hex-Log
- install.bat: installiert die Python-Abhängigkeit hidapi
- start.bat: startet die GUI
- build_exe.bat: baut eine Windows-EXE mit PyInstaller

Voraussetzungen
---------------
1. Windows 11
2. Python 3.11+ von https://www.python.org/downloads/windows/
   Beim Installieren "Add Python to PATH" aktivieren.
3. Schließe LINAK Desk Control und andere Programme, die den Adapter nutzen.

Installation
------------
Doppelklick auf install.bat, danach start.bat.

EXE erstellen
-------------
Doppelklick auf build_exe.bat. Die EXE erscheint in dist\LINAK_USB2LIN06.exe.

Wichtiger Windows-Treiberhinweis
--------------------------------
Das Protokoll-Referenzprojekt verwendet libusb unter Linux. Diese GUI versucht den Zugriff
über HID Feature Reports (hidapi). Ob das unter deinem Windows-Treiber mit dem Adapter
funktioniert, muss geprüft werden. Ändere den Gerätetreiber nicht vorschnell mit Zadig:
Das kann LINAK-Software unbrauchbar machen. Erst Geräte-Manager -> Gerät -> Treiber prüfen
und bei Fehlern die Ausgabe hier teilen.

Sicherheit
----------
- Nur an einem Tisch testen, dessen Bewegungsbereich frei ist.
- Hände, Kabel und Gegenstände von Säulen/Mechanik fernhalten.
- Nicht unbeaufsichtigt fahren lassen.
- Die GUI sendet bei Loslassen ein neues Ziel auf der aktuell gemeldeten Position. Das ist
  kein zertifizierter Not-Aus und kann je nach Steuerbox/Protokoll nicht sofort stoppen.
- Die Beispielreferenz nennt 0..6480 als Positionsbereich, aber die tatsächlichen Grenzen
  sind tischabhängig. Die GUI begrenzt Ziele deshalb nur auf diesen bekannten Referenzbereich;
  prüfe die Werte vorsichtig und verlasse dich nicht auf diese Grenzen als Schutzfunktion.
- Diese Software umgeht keine Anti-Kollisionsfunktion, Endschalter oder Schutzmechanismen.

Hex-Kommandos
-------------
Die GUI protokolliert vollständige Reports, die sie sendet und empfängt. Das sind HID Feature
Reports auf USB-Ebene, nicht automatisch die rohen LIN-Bus-Bytes. Die ersten Bytes werden
aus dem öffentlichen Projekt abgeleitet; nicht am konkreten Windows-Adapter validiert.

Referenzen
----------
https://github.com/gryf/linak-ctrl
https://github.com/UrbanskiDawid/usb2lin06-HID-in-linux-for-LINAK-Desk-Control-Cable
