"""
LINAK USB2LIN06 - Windows-Steuerung (Version 2)

Neu gegenüber Version 1:
  * Zielhöhe per Zahl eingeben und mit einem Klick anfahren
  * Hoch/Runter: Taste gedrückt halten = Tisch fährt durchgehend, Loslassen = Stopp
  * Ein einziger Thread spricht mit dem USB-Adapter (keine parallelen Zugriffe mehr)
  * Totmannschaltung: Meldet sich die Oberfläche nicht mehr, hält der Tisch an
"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

VID = 0x12D3
PID = 0x0002
REPORT_LEN = 64

DEFAULT_MIN = 0
DEFAULT_MAX = 6480          # Referenzwert aus dem öffentlichen Projekt - an eigenen Tisch anpassen!

LOOP_INTERVAL = 0.10        # Sekunden zwischen zwei Statusabfragen/Befehlen (Referenz: 0.2 s)
HOLD_LOOKAHEAD = 400        # Beim Gedrückthalten: Ziel liegt immer so viele Einheiten voraus
HOLD_DEADMAN = 0.5          # Sekunden ohne Lebenszeichen der Oberfläche -> Stopp
ARRIVE_TOLERANCE = 3        # Ziel gilt als erreicht, wenn |Position - Ziel| <= Toleranz
GOTO_STALL_SECONDS = 2.0    # So lange ohne Positionsänderung -> Fahrt abbrechen
STOP_REPEATS = 4            # Stopp-Befehl so oft senden

SETTINGS_FILE = "linak_settings.json"


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- USB
class LinakUSB:
    """USB2LIN06 über HID-Feature-Reports (Protokoll nach gryf/linak-ctrl)."""

    def __init__(self):
        try:
            import hid
        except ImportError as exc:
            raise RuntimeError(
                "Das Paket 'hid' fehlt. Bitte install.bat ausführen (pip install hidapi).") from exc
        self.dev = hid.device()
        self.dev.open(VID, PID)
        self.dev.set_nonblocking(0)
        init = bytearray(REPORT_LEN)
        init[0] = 3      # Report-ID 3: Betriebsmodus
        init[1] = 4
        init[2] = 0
        init[3] = 0xFB
        self.dev.send_feature_report(bytes(init))
        time.sleep(0.5)

    def get_status(self):
        raw = self.dev.get_feature_report(4, REPORT_LEN)
        if not raw or len(raw) < 7:
            raise RuntimeError(f"Ungültige Statusantwort: {raw!r}")
        position = int(raw[4]) + (int(raw[5]) << 8)
        moving = int(raw[6]) > 0
        return position, moving, bytes(raw)

    def send_target(self, position):
        position = max(0, min(0xFFFF, int(position)))
        report = bytearray(REPORT_LEN)
        report[0] = 5    # Report-ID 5: Zielposition
        lo, hi = position & 0xFF, (position >> 8) & 0xFF
        report[1] = report[3] = report[5] = report[7] = lo
        report[2] = report[4] = report[6] = report[8] = hi
        self.dev.send_feature_report(bytes(report))
        return bytes(report)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


# ------------------------------------------------------------------ Steuer-Thread
class DeskWorker(threading.Thread):
    """Einziger Besitzer des USB-Geräts. Die Oberfläche setzt nur Wünsche (goto/hold/stop)."""

    def __init__(self, usb, events, min_pos, max_pos):
        super().__init__(daemon=True)
        self.usb = usb
        self.events = events
        self.min_pos = min_pos
        self.max_pos = max_pos
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._mode = "idle"          # idle | goto | hold | stopping
        self._target = None
        self._dir = 0
        self._deadline = 0.0
        self._stop_left = 0
        self._blocked_dir = 0        # Richtung, in der die Grenze erreicht wurde (bis Taste losgelassen)
        self._goto_started = 0.0
        self._last_pos = None
        self._last_change = 0.0
        self.pos = None

    # ---- Aufrufe aus der Oberfläche (thread-sicher) ----
    def set_limits(self, lo, hi):
        with self._lock:
            self.min_pos, self.max_pos = lo, hi

    def goto(self, target):
        with self._lock:
            target = max(self.min_pos, min(self.max_pos, int(target)))
            self._mode, self._target = "goto", target
            self._goto_started = self._last_change = time.monotonic()
            self._last_pos = self.pos
        self._state(f"Fährt zu {target}")

    def hold(self, direction):
        """Beim Drücken aufrufen und danach alle ~100 ms erneuern, solange die Taste gedrückt ist."""
        with self._lock:
            if direction == self._blocked_dir:
                return
            self._blocked_dir = 0
            starting = self._mode != "hold" or self._dir != direction
            self._mode, self._dir = "hold", direction
            self._deadline = time.monotonic() + HOLD_DEADMAN
        if starting:
            self._state("Fährt hoch (gedrückt halten)" if direction > 0 else "Fährt runter (gedrückt halten)")

    def stop(self):
        with self._lock:
            self._blocked_dir = 0
            active = self._mode in ("goto", "hold")
            if active:
                self._mode, self._stop_left = "stopping", STOP_REPEATS
        if active:
            self._state("Stoppt")

    def shutdown(self):
        self.stop()
        self._quit.set()

    # ---- intern ----
    def _emit(self, *event):
        self.events.put(event)

    def _log(self, msg):
        self._emit("log", msg)

    def _state(self, text):
        self._emit("state", text)

    def _send(self, target):
        raw = self.usb.send_target(target)
        self._emit("tx", target, raw.hex(" ").upper())

    def _finish(self, text):
        self._mode = "idle"
        self._state("Bereit")
        self._log(text)

    def _step(self, pos, moving, now):
        mode = self._mode
        if mode == "idle":
            return

        if mode == "stopping":
            self._send(pos)
            self._stop_left -= 1
            if self._stop_left <= 0:
                self._finish(f"Gestoppt bei {pos}.")
            return

        if mode == "hold":
            if now > self._deadline:
                self._mode, self._stop_left = "stopping", STOP_REPEATS
                self._log("Taste losgelassen (oder Oberfläche reagiert nicht) - Stopp.")
                self._send(pos)
                return
            if (self._dir > 0 and pos >= self.max_pos) or (self._dir < 0 and pos <= self.min_pos):
                self._mode, self._stop_left = "stopping", STOP_REPEATS
                self._blocked_dir = self._dir
                self._log(f"Grenze {self.max_pos if self._dir > 0 else self.min_pos} erreicht.")
                self._send(pos)
                return
            target = pos + self._dir * HOLD_LOOKAHEAD
            self._send(max(self.min_pos, min(self.max_pos, target)))
            return

        if mode == "goto":
            if pos != self._last_pos:
                self._last_pos, self._last_change = pos, now
            if abs(pos - self._target) <= ARRIVE_TOLERANCE and not moving:
                self._send(self._target)
                self._finish(f"Ziel {self._target} erreicht (Position {pos}).")
                return
            if now - self._last_change > GOTO_STALL_SECONDS and now - self._goto_started > 1.0:
                self._mode, self._stop_left = "stopping", STOP_REPEATS
                self._log(f"Keine Bewegung mehr bei {pos} - Fahrt abgebrochen "
                          f"(Ziel {self._target} evtl. außerhalb des Tischbereichs, Hindernis oder Tischsperre).")
                self._send(pos)
                return
            self._send(self._target)

    def run(self):
        failures = 0
        try:
            while not self._quit.is_set() or self._mode == "stopping":
                started = time.monotonic()
                try:
                    pos, moving, raw = self.usb.get_status()
                    failures = 0
                    with self._lock:
                        self.pos = pos
                        self._step(pos, moving, started)
                    self._emit("status", pos, moving, raw.hex(" ").upper())
                except Exception as exc:
                    failures += 1
                    self._log(f"USB-Fehler ({failures}/5): {exc}")
                    if failures >= 5:
                        self._emit("fatal", str(exc))
                        return
                time.sleep(max(0.0, LOOP_INTERVAL - (time.monotonic() - started)))
                if self._quit.is_set() and self._mode != "stopping":
                    break
        finally:
            self.usb.close()


# ------------------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LINAK USB2LIN06 - Tischsteuerung")
        self.geometry("780x700")
        self.minsize(720, 620)
        self.events = queue.Queue()
        self.worker = None
        self.hold_dir = 0
        self.debug_var = tk.BooleanVar(value=False)
        self._load_settings()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda e: self.do_stop())
        self.bind("<space>", lambda e: self.do_stop())
        self.after(50, self._poll_events)
        self.log("Bereit. Adapter anschließen und 'Verbinden' drücken.")
        self.log("Sicherheit: Bewegungsbahn freihalten, Netzstecker/Handschalter in Reichweite. "
                 "Leertaste oder Esc = STOPP.")

    # ---- Einstellungen ----
    def _load_settings(self):
        self.min_pos, self.max_pos, self.last_target = DEFAULT_MIN, DEFAULT_MAX, ""
        try:
            with open(os.path.join(app_dir(), SETTINGS_FILE), encoding="utf-8") as f:
                data = json.load(f)
            self.min_pos = int(data.get("min", DEFAULT_MIN))
            self.max_pos = int(data.get("max", DEFAULT_MAX))
            self.last_target = str(data.get("last_target", ""))
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(os.path.join(app_dir(), SETTINGS_FILE), "w", encoding="utf-8") as f:
                json.dump({"min": self.min_pos, "max": self.max_pos,
                           "last_target": self.target_var.get()}, f)
        except Exception:
            pass

    # ---- Aufbau ----
    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="LINAK USB2LIN06", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="CBD6S · VID 12D3 / PID 0002").pack(anchor="w", pady=(0, 10))

        top = ttk.Frame(root)
        top.pack(fill="x")
        self.connect_btn = ttk.Button(top, text="Verbinden", command=self.connect)
        self.connect_btn.pack(side="left")
        self.disconnect_btn = ttk.Button(top, text="Trennen", command=self.disconnect, state="disabled")
        self.disconnect_btn.pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="Nicht verbunden")
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=10)

        pos_frame = ttk.LabelFrame(root, text="Status", padding=12)
        pos_frame.pack(fill="x", pady=10)
        self.height_var = tk.StringVar(value="-")
        self.moving_var = tk.StringVar(value="Bewegung: unbekannt")
        self.state_var = tk.StringVar(value="Bereit")
        ttk.Label(pos_frame, textvariable=self.height_var, font=("Segoe UI", 30, "bold")).pack(anchor="w")
        ttk.Label(pos_frame, textvariable=self.moving_var).pack(anchor="w")
        ttk.Label(pos_frame, textvariable=self.state_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")

        goto = ttk.LabelFrame(root, text="Zielhöhe anfahren", padding=12)
        goto.pack(fill="x")
        ttk.Label(goto, text="Zielposition:").grid(row=0, column=0, sticky="w")
        self.target_var = tk.StringVar(value=self.last_target)
        self.target_entry = ttk.Entry(goto, textvariable=self.target_var, width=10, font=("Segoe UI", 14))
        self.target_entry.grid(row=0, column=1, padx=8)
        self.target_entry.bind("<Return>", lambda e: self.do_goto())
        self.goto_btn = ttk.Button(goto, text="Fahren", command=self.do_goto, state="disabled")
        self.goto_btn.grid(row=0, column=2, ipadx=12, ipady=4)
        self.stop_btn = ttk.Button(goto, text="STOPP", command=self.do_stop, state="disabled")
        self.stop_btn.grid(row=0, column=3, padx=(16, 0), ipadx=12, ipady=4)

        controls = ttk.LabelFrame(root, text="Manuell (Taste gedrückt halten)", padding=12)
        controls.pack(fill="x", pady=10)
        self.up_btn = ttk.Button(controls, text="▲  HOCH")
        self.up_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=14)
        self.down_btn = ttk.Button(controls, text="▼  RUNTER")
        self.down_btn.grid(row=0, column=1, sticky="ew", ipady=14)
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        for btn, direction in ((self.up_btn, 1), (self.down_btn, -1)):
            btn.bind("<ButtonPress-1>", lambda e, d=direction: self.hold_start(d))
            btn.bind("<ButtonRelease-1>", self.hold_end)
            btn.state(["disabled"])

        limits = ttk.LabelFrame(root, text="Grenzen (Software-Begrenzung, tischabhängig)", padding=8)
        limits.pack(fill="x")
        ttk.Label(limits, text="Min:").pack(side="left")
        self.min_var = tk.StringVar(value=str(self.min_pos))
        ttk.Entry(limits, textvariable=self.min_var, width=7).pack(side="left", padx=(4, 12))
        ttk.Label(limits, text="Max:").pack(side="left")
        self.max_var = tk.StringVar(value=str(self.max_pos))
        ttk.Entry(limits, textvariable=self.max_var, width=7).pack(side="left", padx=(4, 12))
        ttk.Button(limits, text="Übernehmen", command=self.apply_limits).pack(side="left")
        ttk.Checkbutton(limits, text="Hex-Protokoll anzeigen", variable=self.debug_var).pack(side="right")

        log_frame = ttk.LabelFrame(root, text="Protokoll", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def log(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {msg}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_connected(self, connected):
        on, off = ["!disabled"], ["disabled"]
        self.connect_btn.state(off if connected else on)
        self.disconnect_btn.state(on if connected else off)
        for b in (self.up_btn, self.down_btn, self.goto_btn, self.stop_btn):
            b.state(on if connected else off)

    # ---- Verbindung ----
    def connect(self):
        if self.worker:
            return
        try:
            usb = LinakUSB()
            pos, moving, raw = usb.get_status()
        except Exception as exc:
            self.log(f"VERBINDUNGSFEHLER: {exc}")
            messagebox.showerror("USB-Verbindung fehlgeschlagen",
                                 f"{exc}\n\nPrüfe, ob der Adapter erkannt wird und ob eine andere "
                                 "LINAK-Anwendung (z. B. Desk Control) ihn gerade verwendet.")
            return
        self.worker = DeskWorker(usb, self.events, self.min_pos, self.max_pos)
        self.worker.pos = pos
        self.worker.start()
        self.status_var.set("Verbunden")
        self._set_connected(True)
        self.log(f"Verbunden. Startposition {pos}. RX: {raw.hex(' ').upper()}")

    def disconnect(self):
        self.hold_dir = 0
        if self.worker:
            self.worker.shutdown()
            self.worker.join(timeout=2.0)
            self.worker = None
        self.status_var.set("Nicht verbunden")
        self.height_var.set("-")
        self.moving_var.set("Bewegung: unbekannt")
        self.state_var.set("Bereit")
        self._set_connected(False)
        self.log("Verbindung getrennt.")

    # ---- Bedienung ----
    def apply_limits(self):
        try:
            lo, hi = int(self.min_var.get()), int(self.max_var.get())
            if not 0 <= lo < hi <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Ungültig", "Min und Max müssen ganze Zahlen sein (0 <= Min < Max <= 65535).")
            return
        self.min_pos, self.max_pos = lo, hi
        if self.worker:
            self.worker.set_limits(lo, hi)
        self._save_settings()
        self.log(f"Grenzen gesetzt: {lo} .. {hi}")

    def do_goto(self):
        if not self.worker:
            return
        try:
            target = int(self.target_var.get().strip())
        except ValueError:
            messagebox.showerror("Ungültige Eingabe", "Bitte eine ganze Zahl als Zielposition eingeben.")
            return
        if not self.min_pos <= target <= self.max_pos:
            messagebox.showerror("Außerhalb der Grenzen",
                                 f"Ziel {target} liegt außerhalb {self.min_pos} .. {self.max_pos}.")
            return
        self.hold_dir = 0
        self._save_settings()
        self.log(f"Fahre zu Position {target}.")
        self.worker.goto(target)

    def do_stop(self):
        self.hold_dir = 0
        if self.worker:
            self.log("STOPP angefordert.")
            self.worker.stop()

    def hold_start(self, direction):
        if not self.worker or self.hold_dir:
            return
        self.hold_dir = direction
        self.log("Taste gedrückt: " + ("HOCH" if direction > 0 else "RUNTER"))
        self._hold_tick()

    def _hold_tick(self):
        if self.hold_dir and self.worker:
            self.worker.hold(self.hold_dir)
            self.after(100, self._hold_tick)

    def hold_end(self, event=None):
        if not self.hold_dir:
            return
        self.hold_dir = 0
        if self.worker:
            self.worker.stop()

    # ---- Ereignisse vom Steuer-Thread ----
    def _poll_events(self):
        try:
            while True:
                kind, *args = self.events.get_nowait()
                if kind == "status":
                    pos, moving, raw = args
                    self.height_var.set(f"{pos}")
                    self.moving_var.set("Bewegung: ja" if moving else "Bewegung: nein")
                    if self.debug_var.get():
                        self.log(f"RX {raw}")
                elif kind == "tx":
                    if self.debug_var.get():
                        self.log(f"TX Ziel={args[0]}: {args[1]}")
                elif kind == "state":
                    self.state_var.set(args[0])
                elif kind == "log":
                    self.log(args[0])
                elif kind == "fatal":
                    self.log(f"Verbindung verloren: {args[0]}")
                    self.worker = None
                    self.hold_dir = 0
                    self.status_var.set("Nicht verbunden")
                    self._set_connected(False)
                    messagebox.showerror("USB-Fehler", f"Verbindung zum Adapter verloren:\n{args[0]}")
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def on_close(self):
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
