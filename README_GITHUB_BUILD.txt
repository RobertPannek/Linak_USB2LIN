LINAK USB2LIN06 – GitHub-Build-Paket für Windows-EXE
====================================================

Ziel: EXE bauen lassen, ohne Python auf dem eigenen Windows-PC zu installieren.

WICHTIG
-------
Dieses Paket baut den vorhandenen experimentellen Python-Quellcode auf einem von GitHub
bereitgestellten Windows-Rechner. Die USB-Kommunikation ist damit NICHT automatisch am
echten USB2LIN06/CBD6S validiert. Benutze die Software zunächst vorsichtig und verlasse dich
nicht auf sie als Not-Aus oder Sicherheitseinrichtung.

SCHRITT 1 – ZIP entpacken
-------------------------
Entpacke dieses ZIP auf deinem PC.

SCHRITT 2 – GitHub-Repository anlegen
-------------------------------------
1. Melde dich bei https://github.com an oder erstelle ein Konto.
2. Öffne https://github.com/new
3. Repository name: LINAK-USB2LIN06-Windows
4. Wähle "Private" (empfohlen, wenn du den Code nicht öffentlich teilen willst).
5. Klicke "Create repository".

SCHRITT 3 – Dateien hochladen
-----------------------------
1. Öffne das neue Repository.
2. Wähle "Add file" -> "Upload files".
3. Lade ALLE Dateien und Ordner aus dem entpackten Paket hoch.
   Wichtig: Auch den versteckten Ordner ".github" samt "workflows/build-windows.yml"
   hochladen. Falls dein Browser versteckte Ordner beim Auswählen ausblendet, ziehe den
   gesamten Inhalt aus dem Datei-Explorer in den Upload-Bereich oder verwende GitHub Desktop.
4. Klicke "Commit changes".

SCHRITT 4 – Build abwarten
--------------------------
1. Öffne im Repository den Tab "Actions".
2. Wähle links "Build Windows EXE".
3. Öffne den neuesten erfolgreichen Lauf (grüner Haken).
4. Unten im Abschnitt "Artifacts" klicke auf "LINAK_USB2LIN06-Windows-EXE".
5. Lade das Artifact-ZIP herunter und entpacke es. Darin liegt LINAK_USB2LIN06.exe.

Wenn Actions deaktiviert sind, öffne den Hinweis im Actions-Tab und aktiviere Workflows für
das Repository. Bei einem privaten Repository können GitHub-Kontingente gelten.

SICHERHEIT UND DIAGNOSE
-----------------------
- Die EXE wird aus dem bereitgestellten Quellcode gebaut; sie ist nicht digital signiert.
  Windows SmartScreen kann deshalb warnen.
- Das Programm wurde hier nicht am realen Adapter getestet.
- Schließe andere Programme, die den Adapter verwenden.
- Teste nur bei freiem Bewegungsbereich und erreichbarer Originalbedienung.
- Das Loslassen sendet ein neues Positionsziel; dies ist kein garantierter Not-Aus.
- Falls die Verbindung scheitert, teile die genaue Fehlermeldung mit. Ändere den USB-Treiber
  nicht vorschnell mit Zadig.

Dateien
-------
- linak_gui.pyw: GUI-Quellcode
- .github/workflows/build-windows.yml: automatischer Windows-Build
- install.bat / start.bat / build_exe.bat: lokale Alternativen (lokal ist Python nötig)
