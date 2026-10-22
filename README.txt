LINAK USB2LIN06 Windows-Steuerung - Version 2
=============================================

Neu in Version 2
----------------
1. ZIELHÖHE PER ZAHL: Zahl ins Feld "Zielposition" eintragen, "Fahren" klicken (oder Enter).
   Der Tisch fährt durchgehend zum Ziel und hält dort an. Meldungen im Protokoll:
   "Ziel erreicht" oder "Keine Bewegung mehr ... abgebrochen".
2. DURCHGEHEND FAHREN: HOCH/RUNTER-Taste gedrückt halten = Tisch fährt ohne Unterbrechung.
   Loslassen = Stopp.
3. STOPP-Taste, sowie Leertaste/Esc = sofortiger Stopp.
4. Grenzen (Min/Max) sind einstellbar und werden in linak_settings.json (neben der EXE/dem
   Skript) gespeichert. Standard 0..6480 ist nur ein Referenzwert - an den eigenen Tisch anpassen.
5. Totmannschaltung: Meldet sich die Oberfläche beim Gedrückthalten nicht mehr, hält der Tisch an.
6. Ein einziger Thread spricht mit dem USB-Adapter (Version 1 griff aus mehreren Threads
   gleichzeitig zu, was zum "Zucken" beitragen konnte).

Bedienung
---------
Verbinden -> Position erscheint -> Ziel eingeben -> Fahren.
"Hex-Protokoll anzeigen" (Checkbox) zeigt gesendete/empfangene USB-Reports zur Fehlersuche.

Feineinstellungen (oben in linak_gui.pyw)
-----------------------------------------
HOLD_LOOKAHEAD  Beim Gedrückthalten liegt das Ziel immer so weit vor dem Tisch (Standard 400).
                Größer = flüssiger, aber längerer Nachlauf, falls das Programm hängt.
LOOP_INTERVAL   Sendeintervall in Sekunden (Standard 0.1).

Installation / EXE-Build
------------------------
Unverändert: install.bat, start.bat, build_exe.bat bzw. GitHub-Build laut README_GITHUB_BUILD.txt.
Schließe LINAK Desk Control und andere Programme, die den Adapter nutzen.

Sicherheit
----------
- Nur bei freier Bewegungsbahn testen, nicht unbeaufsichtigt fahren lassen.
- STOPP ist eine Software-Anforderung (neues Ziel = aktuelle Position), kein zertifizierter Not-Aus.
- Anti-Kollisions- und Endlagenfunktionen des Tisches bleiben aktiv und werden nicht umgangen.
- Protokollreferenz: https://github.com/gryf/linak-ctrl
