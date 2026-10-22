Tischsteuerung v3 - LINAK DESKLINE (CBD6S) über USB2LIN06
==========================================================

Eigenständiger Nachbau der Funktionen von "Desk Control Basic" (eigener Code und eigenes Design,
kein LINAK-Code, keine LINAK-Grafiken). Das USB-Protokoll wurde so umgesetzt, wie es die
Original-Software auf dem Adapter spricht (aus der Software ausgelesen):

  Init    Report 3 = 03 04 00 FB, danach sofort STOPP
  Status  Report 4 lesen: Position = Byte 4/5 (0,1 mm), Byte 6 = Fehlerbits
  Befehl  Report 5 = 05 + 4x (lo, hi); Takt 200 ms
          HOCH 0x8000 · RUNTER 0x7FFF · STOPP 0x8001 · Zielhöhe = Position in 0,1 mm

Funktionen
----------
- Große Höhenanzeige (cm), Rohposition darunter
- HOCH / RUNTER: Taste gedrückt halten, Loslassen = STOPP (sendet 0x8001)
- Zielhöhe per Zahl (cm) mit einem Klick, STOPP / Leertaste / Esc jederzeit
- 3 Speicherpositionen (Anfahren, aktuelle Höhe speichern, eigene Namen)
- Erinnerungen Sitzen/Stehen mit "Anfahren", "Später", "Überspringen"
- Statistik: Steh-/Sitzzeit, Wechsel, Kalorien (Schätzung), 14-Tage-Diagramm, CSV-Export
- Fehleranzeige wie im Original: E01 Position verloren, E02/E03 Überlast
- Fahrt blockiert bei E01 (erst referenzieren)
- Totmannschaltung: reagiert die Oberfläche nicht mehr, hält der Tisch an
- Abbruch bei ausbleibender Bewegung (Hindernis, Sperre, Ziel außerhalb des Bereichs)

Erste Schritte
--------------
1. Einstellungen -> "Höhe bei Position 0": Tischflächenhöhe ganz unten eintragen (damit die Anzeige
   der echten Höhe entspricht). Speicherhöhen und Schwelle 'Stehen' anpassen.
2. Verbinden. Zuerst mit HOCH/RUNTER kurz testen, dann Zielfahrt mit kleinem Abstand.
3. Einstellung "Zielfahrt mit einem Klick": AUS = Zielfahrt nur solange die Taste gedrückt ist
   (so arbeitet das Original). Empfohlen für die ersten Tests am 2-Controller-Tisch.

Daten liegen in %APPDATA%\Tischsteuerung (settings.json, stats.db).

Installation / Start
--------------------
Lokal mit Python: install.bat, dann start.bat. EXE: build_exe.bat oder GitHub-Build
(.github/workflows/build-windows.yml, Anleitung wie bisher: Repository anlegen, alle Dateien
inkl. .github hochladen, unter "Actions" die EXE herunterladen).
Schließe Desk Control und andere Programme, die den Adapter nutzen.

Sicherheit
----------
Freie Bewegungsbahn, Originalbedienung in Reichweite. STOPP ist ein Software-Befehl und kein
zertifizierter Not-Aus. Endlagen/Überlastschutz des Tisches bleiben aktiv und werden nicht umgangen.
Nicht am echten Tisch automatisiert getestet: die Logik wurde gegen einen simulierten Tisch
geprüft, die Oberfläche nur ohne Display durchlaufen.
