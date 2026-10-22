"""
Tischsteuerung für LINAK DESKLINE (CBD6S) über USB2LIN06 - Version 3

Eigenständige Neuentwicklung (eigener Code, eigenes Design). Das USB-Protokoll entspricht dem,
was die Original-Software "Desk Control Basic" auf dem Adapter spricht:

    Init     : Feature-Report 3  = 03 04 00 FB ...   danach STOPP
    Status   : Feature-Report 4 lesen  (Position = Byte 4/5 in 0,1 mm, Byte 6 = Fehlerbits)
    Befehl   : Feature-Report 5 = 05 lo hi lo hi lo hi lo hi ...   (Zielwert, 16 Bit)
                 HOCH   = 0x8000      RUNTER = 0x7FFF      STOPP = 0x8001
                 Zielhöhe = Position in 0,1 mm
    Takt     : Befehl wird alle 200 ms wiederholt, solange eine Fahrt läuft.

Funktionen: Höhenanzeige, Hoch/Runter (gedrückt halten), Zielhöhe per Zahl, 3 Speicherpositionen,
Erinnerungen (Sitzen/Stehen), Statistik (Steh-/Sitzzeit, Wechsel, Kalorien), CSV-Export, Fehleranzeige.
"""
import csv
import json
import os
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Tischsteuerung"
VID, PID = 0x12D3, 0x0002
REPORT_LEN = 64

CMD_UP = 0x8000
CMD_DOWN = 0x7FFF
CMD_STOP = 0x8001

CYCLE = 0.2                 # Sekunden: Status lesen + Befehl senden (wie Original: 200 ms)
HOLD_DEADMAN = 0.7          # Sekunden ohne Lebenszeichen der Oberfläche -> Stopp
ARRIVE_TOL = 5              # 0,5 mm
STALL_SECONDS = 3.0         # so lange keine Positionsänderung -> Zielfahrt abbrechen
STOP_REPEATS = 3

DEFAULTS = {
    "offset_cm": 0.0,           # Höhe der Tischfläche bei Position 0 (zum Anzeigen der echten Höhe)
    "limit_min_cm": 0.0,        # Software-Grenzen (Gesamthöhe in cm); Tisch-Endlagen bleiben aktiv
    "limit_max_cm": 200.0,
    "memory": [72.0, 110.0, 90.0],
    "memory_names": ["Sitzen", "Stehen", "Mitte"],
    "one_click_goto": True,     # False = Zielfahrt nur solange Taste gedrückt (wie Original)
    "weight_kg": 75.0,
    "stand_threshold_cm": 95.0,
    "remind_enabled": True,
    "remind_sit_min": 45,
    "remind_stand_min": 30,
    "snooze_min": 10,
    "hex_log": False,
}

# --------------------------------------------------------------------------- Pfade / Einstellungen
def data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return os.path.dirname(os.path.abspath(sys.argv[0]))


class Settings:
    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "settings.json")
        self.values = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k in DEFAULTS:
                    self.values[k] = v
        except Exception:
            pass

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.values, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


# ------------------------------------------------------------------------------- Statistik (SQLite)
class Stats:
    """Tageswerte: Sitz-/Stehsekunden, Anzahl Wechsel zum Stehen."""

    def __init__(self, path=None):
        self.db = sqlite3.connect(path or os.path.join(data_dir(), "stats.db"), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, sit REAL DEFAULT 0, "
                        "stand REAL DEFAULT 0, stands INTEGER DEFAULT 0)")
        self.db.commit()

    def add(self, day, sit=0.0, stand=0.0, stands=0):
        self.db.execute("INSERT OR IGNORE INTO daily(day) VALUES (?)", (day,))
        self.db.execute("UPDATE daily SET sit = sit + ?, stand = stand + ?, stands = stands + ? WHERE day = ?",
                        (sit, stand, stands, day))
        self.db.commit()

    def range(self, start, end):
        cur = self.db.execute("SELECT day, sit, stand, stands FROM daily WHERE day >= ? AND day <= ? ORDER BY day",
                              (start.isoformat(), end.isoformat()))
        return cur.fetchall()

    def total(self, start, end):
        rows = self.range(start, end)
        return (sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows))

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass


def kcal_for(stand_seconds, weight_kg):
    """Grobe Schätzung des Mehrverbrauchs im Stehen gegenüber Sitzen (~0,0022 kcal je kg und Minute)."""
    return stand_seconds / 60.0 * weight_kg * 0.0022


# ------------------------------------------------------------------------------------------ USB
class LinakUSB:
    def __init__(self):
        try:
            import hid
        except ImportError as exc:
            raise RuntimeError("Das Paket 'hidapi' fehlt. Bitte install.bat ausführen "
                               "(pip install hidapi).") from exc
        self.dev = hid.device()
        self.dev.open(VID, PID)
        self.dev.set_nonblocking(0)
        init = bytearray(REPORT_LEN)
        init[0], init[1], init[2], init[3] = 3, 4, 0, 0xFB
        self.dev.send_feature_report(bytes(init))
        self.send_command(CMD_STOP)          # wie das Original: nach dem Init sofort STOPP

    def read_status(self):
        raw = self.dev.get_feature_report(4, REPORT_LEN)
        if not raw or len(raw) < 8:
            raise RuntimeError(f"Ungültige Statusantwort: {raw!r}")
        raw = bytes(raw)
        position = raw[4] | (raw[5] << 8)                 # 0,1 mm
        return position, raw

    def send_command(self, value):
        value = int(value) & 0xFFFF
        lo, hi = value & 0xFF, value >> 8
        report = bytearray(REPORT_LEN)
        report[0] = 5
        for i in (1, 3, 5, 7):
            report[i] = lo
        for i in (2, 4, 6, 8):
            report[i] = hi
        self.dev.send_feature_report(bytes(report))
        return bytes(report)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


def decode_errors(raw):
    """Fehlerauswertung wie im Original: Byte 6 = Bitfeld, Byte 3 Bit 1 = Fehlerliste ab Byte 34."""
    errors = []
    b6 = raw[6]
    if b6 & 1:
        errors.append(("E01", "Position verloren - Tisch neu referenzieren (am Handschalter ganz herunterfahren)"))
    if b6 & 4:
        errors.append(("E02", "Überlast beim Hochfahren"))
    if b6 & 8:
        errors.append(("E03", "Überlast beim Runterfahren"))
    if raw[3] & 2 and len(raw) >= 40 and raw[32] == 1:
        for code in raw[34:40]:
            if code:
                errors.append((f"E{code:02d}", "Fehlercode der Steuerbox"))
    return errors


# ---------------------------------------------------------------------------------- Steuer-Thread
class DeskWorker(threading.Thread):
    """Einziger Besitzer des USB-Geräts. Die Oberfläche setzt nur Wünsche."""

    def __init__(self, usb, events, limits_raw):
        super().__init__(daemon=True)
        self.usb, self.events = usb, events
        self.lo_raw, self.hi_raw = limits_raw
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self.mode = "idle"            # idle | up | down | goto | stopping
        self._goto_target = 0
        self._held_goto = False
        self._deadline = 0.0
        self._stop_left = 0
        self._goto_started = 0.0
        self._last_pos = None
        self._last_change = 0.0
        self._blocked = None
        self.pos = None

    # ---- Aufrufe aus der Oberfläche
    def set_limits(self, lo_raw, hi_raw):
        with self._lock:
            self.lo_raw, self.hi_raw = lo_raw, hi_raw

    def hold(self, direction):
        with self._lock:
            if self._blocked == direction:
                return
            self._blocked = None
            mode = "up" if direction > 0 else "down"
            changed = self.mode != mode
            self.mode, self._deadline = mode, time.monotonic() + HOLD_DEADMAN
        if changed:
            self._state("Fährt hoch" if direction > 0 else "Fährt runter")

    def goto(self, target_raw, held=False):
        with self._lock:
            target_raw = max(self.lo_raw, min(self.hi_raw, int(target_raw)))
            self.mode, self._goto_target, self._held_goto = "goto", target_raw, held
            self._goto_started = self._last_change = time.monotonic()
            self._deadline = self._goto_started + HOLD_DEADMAN
            self._last_pos = self.pos
        self._state(f"Fährt zu {target_raw / 100:.1f}")

    def keepalive(self):
        with self._lock:
            self._deadline = time.monotonic() + HOLD_DEADMAN

    def stop(self):
        with self._lock:
            self._blocked = None
            active = self.mode in ("up", "down", "goto")
            if active:
                self.mode, self._stop_left = "stopping", STOP_REPEATS
        if active:
            self._state("Stoppt")

    def shutdown(self):
        self.stop()
        self._quit.set()

    # ---- intern
    def _emit(self, *event):
        self.events.put(event)

    def _log(self, msg):
        self._emit("log", msg)

    def _state(self, text):
        self._emit("state", text)

    def _send(self, value):
        raw = self.usb.send_command(value)
        self._emit("tx", value, raw.hex(" ").upper())

    def _begin_stop(self, pos, reason):
        self.mode, self._stop_left = "stopping", STOP_REPEATS
        self._log(reason)
        self._send(CMD_STOP)

    def _step(self, pos, now):
        mode = self.mode
        if mode == "idle":
            return
        if mode == "stopping":
            self._send(CMD_STOP)
            self._stop_left -= 1
            if self._stop_left <= 0:
                self.mode = "idle"
                self._state("Bereit")
                self._log(f"Gestoppt bei {pos / 100:.1f}.")
            return
        if mode in ("up", "down") or (mode == "goto" and self._held_goto):
            if now > self._deadline:
                self._begin_stop(pos, "Taste losgelassen - Stopp.")
                return
        if mode == "up":
            if pos >= self.hi_raw:
                self._blocked = 1
                self._begin_stop(pos, "Obere Software-Grenze erreicht.")
                return
            self._send(CMD_UP)
        elif mode == "down":
            if pos <= self.lo_raw:
                self._blocked = -1
                self._begin_stop(pos, "Untere Software-Grenze erreicht.")
                return
            self._send(CMD_DOWN)
        elif mode == "goto":
            if pos != self._last_pos:
                self._last_pos, self._last_change = pos, now
            if abs(pos - self._goto_target) <= ARRIVE_TOL:
                self._begin_stop(pos, f"Ziel {self._goto_target / 100:.1f} erreicht.")
                return
            if now - self._last_change > STALL_SECONDS and now - self._goto_started > 1.5:
                self._begin_stop(pos, f"Keine Bewegung mehr bei {pos / 100:.1f} - Fahrt abgebrochen "
                                      "(Ziel außerhalb des Bereichs, Hindernis, Sperre oder Fehler).")
                return
            self._send(self._goto_target)

    def run(self):
        failures = 0
        try:
            while True:
                started = time.monotonic()
                if self._quit.is_set() and self.mode != "stopping":
                    break
                try:
                    pos, raw = self.usb.read_status()
                    failures = 0
                    with self._lock:
                        self.pos = pos
                        self._step(pos, started)
                    self._emit("status", pos, raw.hex(" ").upper(), decode_errors(raw))
                except Exception as exc:
                    failures += 1
                    self._log(f"USB-Fehler ({failures}/5): {exc}")
                    if failures >= 5:
                        self._emit("fatal", str(exc))
                        return
                time.sleep(max(0.0, CYCLE - (time.monotonic() - started)))
        finally:
            self.usb.close()


# ==================================================================================== Oberfläche
BG, SIDEBAR, CARD, CARD2 = "#12141a", "#0d0f14", "#1b1e26", "#252936"
TEXT, MUTED = "#eceff4", "#8b93a5"
ACCENT, OK, DANGER, WARN = "#3b82f6", "#22c55e", "#ef4444", "#f59e0b"
FONT = "Segoe UI"


def make_button(parent, text, bg=CARD2, fg=TEXT, command=None, size=11, bold=True, padx=18, pady=10, hover=None):
    hover = hover or ACCENT
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=hover,
                  activeforeground="white", relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                  font=(FONT, size, "bold" if bold else "normal"), padx=padx, pady=pady)
    b.bind("<Enter>", lambda e: b.configure(bg=hover) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.configure(bg=bg))
    return b


def card(parent, **kw):
    return tk.Frame(parent, bg=CARD, padx=kw.pop("padx", 18), pady=kw.pop("pady", 16), **kw)


def parse_cm(text):
    return float(text.strip().replace(",", ".").replace("cm", "").strip())


class Notification(tk.Toplevel):
    def __init__(self, app, title, message, actions):
        super().__init__(app)
        self.configure(bg=CARD, padx=20, pady=16)
        self.title(title)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        tk.Label(self, text=title, bg=CARD, fg=TEXT, font=(FONT, 14, "bold")).pack(anchor="w")
        tk.Label(self, text=message, bg=CARD, fg=MUTED, font=(FONT, 11), wraplength=330,
                 justify="left").pack(anchor="w", pady=(6, 14))
        row = tk.Frame(self, bg=CARD)
        row.pack(fill="x")
        for label, color, cb in actions:
            make_button(row, label, bg=color, command=lambda c=cb: (self.destroy(), c()), size=10,
                        padx=12, pady=7).pack(side="left", padx=(0, 8))
        self.update_idletasks()
        x = self.winfo_screenwidth() - self.winfo_reqwidth() - 30
        y = self.winfo_screenheight() - self.winfo_reqheight() - 90
        self.geometry(f"+{x}+{y}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tischsteuerung")
        self.geometry("980x700")
        self.minsize(900, 640)
        self.configure(bg=BG)
        self.settings = Settings()
        self.stats = Stats()
        self.events = queue.Queue()
        self.worker = None
        self.hold_dir = 0
        self.held_goto = False
        self.pos = None
        self.errors = []
        self.last_tick = None
        self.pending = {"sit": 0.0, "stand": 0.0, "stands": 0}
        self.last_flush = time.monotonic()
        self.posture = None
        self.posture_since = time.monotonic()
        self.snooze_until = 0.0
        self.notification = None
        self.current_day = date.today().isoformat()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda e: self.do_stop())
        self.bind("<space>", lambda e: self.do_stop())
        self.after(50, self._poll)
        self.after(1000, self._housekeeping)
        self.show_page("control")
        self.log("Bereit. USB2LIN06 anschließen und oben auf 'Verbinden' klicken.")
        self.log("Leertaste oder Esc = STOPP. Bewegungsbahn freihalten.")

    # ---------------------------------------------------------------- Umrechnung
    def raw_to_cm(self, raw):
        return raw / 100.0 + self.settings["offset_cm"]

    def cm_to_raw(self, cm):
        return int(round((cm - self.settings["offset_cm"]) * 100))

    def limits_raw(self):
        lo = max(0, self.cm_to_raw(self.settings["limit_min_cm"]))
        hi = min(0x7FF0, self.cm_to_raw(self.settings["limit_max_cm"]))
        return lo, hi

    # ---------------------------------------------------------------- Aufbau
    def _build(self):
        side = tk.Frame(self, bg=SIDEBAR, width=190)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Tisch-\nsteuerung", bg=SIDEBAR, fg=TEXT, font=(FONT, 18, "bold"),
                 justify="left").pack(anchor="w", padx=20, pady=(26, 4))
        tk.Label(side, text="USB2LIN06 · CBD6S", bg=SIDEBAR, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=20)
        self.nav = {}
        for key, label in (("control", "Steuerung"), ("stats", "Statistik"), ("settings", "Einstellungen")):
            b = tk.Button(side, text="   " + label, anchor="w", relief="flat", bd=0, bg=SIDEBAR, fg=MUTED,
                          activebackground=CARD, activeforeground=TEXT, font=(FONT, 12), cursor="hand2",
                          padx=10, pady=10, command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=(18 if key == "control" else 2, 0))
            self.nav[key] = b

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        top = tk.Frame(main, bg=BG)
        top.pack(fill="x", padx=24, pady=(18, 0))
        self.conn_dot = tk.Label(top, text="●", bg=BG, fg=DANGER, font=(FONT, 14))
        self.conn_dot.pack(side="left")
        self.conn_var = tk.StringVar(value="Nicht verbunden")
        tk.Label(top, textvariable=self.conn_var, bg=BG, fg=TEXT, font=(FONT, 11)).pack(side="left", padx=6)
        self.conn_btn = make_button(top, "Verbinden", bg=ACCENT, command=self.toggle_connect, size=10,
                                    padx=14, pady=6, hover="#2563eb")
        self.conn_btn.pack(side="right")

        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(main, textvariable=self.error_var, bg=DANGER, fg="white", font=(FONT, 10, "bold"),
                                    wraplength=700, justify="left", padx=14, pady=8)

        self.container = tk.Frame(main, bg=BG)
        self.container.pack(fill="both", expand=True, padx=24, pady=16)
        self.pages = {"control": self._page_control(), "stats": self._page_stats(),
                      "settings": self._page_settings()}

    def show_page(self, key):
        for k, f in self.pages.items():
            f.pack_forget()
            self.nav[k].configure(bg=SIDEBAR, fg=MUTED)
        self.pages[key].pack(fill="both", expand=True)
        self.nav[key].configure(bg=CARD, fg=TEXT)
        if key == "stats":
            self.refresh_stats()
        if key == "settings":
            self.load_settings_into_form()

    # ---------------------------------------------------------------- Seite: Steuerung
    def _page_control(self):
        page = tk.Frame(self.container, bg=BG)

        hero = card(page, pady=20)
        hero.pack(fill="x")
        self.height_var = tk.StringVar(value="--.-")
        row = tk.Frame(hero, bg=CARD)
        row.pack(anchor="w")
        tk.Label(row, textvariable=self.height_var, bg=CARD, fg=TEXT, font=(FONT, 60, "bold")).pack(side="left")
        tk.Label(row, text=" cm", bg=CARD, fg=MUTED, font=(FONT, 22)).pack(side="left", anchor="s", pady=(0, 14))
        self.sub_var = tk.StringVar(value="Position (roh): -")
        tk.Label(hero, textvariable=self.sub_var, bg=CARD, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        self.state_var = tk.StringVar(value="Bereit")
        self.state_label = tk.Label(hero, textvariable=self.state_var, bg=CARD, fg=OK, font=(FONT, 11, "bold"))
        self.state_label.pack(anchor="w", pady=(4, 0))

        mid = tk.Frame(page, bg=BG)
        mid.pack(fill="x", pady=14)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)
        self.up_btn = make_button(mid, "▲   HOCH", bg=CARD2, size=16, pady=22)
        self.up_btn.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.down_btn = make_button(mid, "▼   RUNTER", bg=CARD2, size=16, pady=22)
        self.down_btn.grid(row=0, column=1, sticky="ew", padx=(7, 0))
        for btn, d in ((self.up_btn, 1), (self.down_btn, -1)):
            btn.bind("<ButtonPress-1>", lambda e, d=d: self.hold_start(d))
            btn.bind("<ButtonRelease-1>", lambda e: self.hold_end())
        tk.Label(page, text="Taste gedrückt halten = Tisch fährt · Loslassen = Stopp", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w")

        mem = card(page)
        mem.pack(fill="x", pady=(12, 0))
        tk.Label(mem, text="Speicherpositionen", bg=CARD, fg=TEXT, font=(FONT, 12, "bold")).pack(anchor="w")
        self.mem_buttons, self.mem_labels = [], []
        mrow = tk.Frame(mem, bg=CARD)
        mrow.pack(fill="x", pady=(10, 0))
        for i in range(3):
            mrow.columnconfigure(i, weight=1)
            cell = tk.Frame(mrow, bg=CARD2, padx=12, pady=10)
            cell.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            name = tk.Label(cell, text="", bg=CARD2, fg=MUTED, font=(FONT, 9))
            name.pack(anchor="w")
            val = tk.Label(cell, text="", bg=CARD2, fg=TEXT, font=(FONT, 16, "bold"))
            val.pack(anchor="w")
            btns = tk.Frame(cell, bg=CARD2)
            btns.pack(fill="x", pady=(8, 0))
            go = make_button(btns, "Anfahren", bg=ACCENT, size=9, padx=10, pady=5, hover="#2563eb")
            go.pack(side="left")
            go.bind("<ButtonPress-1>", lambda e, i=i: self.goto_memory(i))
            go.bind("<ButtonRelease-1>", lambda e: self.goto_release())
            make_button(btns, "Aktuelle Höhe speichern", bg=CARD, fg=MUTED, size=8, bold=False, padx=8, pady=5,
                        command=lambda i=i: self.save_memory(i)).pack(side="right")
            self.mem_labels.append((name, val))
            self.mem_buttons.append(go)

        goto = card(page)
        goto.pack(fill="x", pady=(12, 0))
        tk.Label(goto, text="Zielhöhe anfahren", bg=CARD, fg=TEXT, font=(FONT, 12, "bold")).pack(anchor="w")
        grow = tk.Frame(goto, bg=CARD)
        grow.pack(fill="x", pady=(10, 0))
        self.target_var = tk.StringVar(value="")
        self.target_entry = tk.Entry(grow, textvariable=self.target_var, width=8, font=(FONT, 18, "bold"),
                                     bg=CARD2, fg=TEXT, insertbackground=TEXT, relief="flat", justify="center")
        self.target_entry.pack(side="left", ipady=6)
        self.target_entry.bind("<Return>", lambda e: self.goto_entry())
        tk.Label(grow, text=" cm", bg=CARD, fg=MUTED, font=(FONT, 13)).pack(side="left")
        self.go_btn = make_button(grow, "Fahren", bg=ACCENT, size=12, padx=26, pady=10, hover="#2563eb")
        self.go_btn.pack(side="left", padx=14)
        self.go_btn.bind("<ButtonPress-1>", lambda e: self.goto_entry())
        self.go_btn.bind("<ButtonRelease-1>", lambda e: self.goto_release())
        make_button(grow, "STOPP", bg=DANGER, size=12, padx=26, pady=10, hover="#dc2626",
                    command=self.do_stop).pack(side="right")
        self.goto_hint = tk.Label(goto, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.goto_hint.pack(anchor="w", pady=(8, 0))
        return page

    # ---------------------------------------------------------------- Seite: Statistik
    def _page_stats(self):
        page = tk.Frame(self.container, bg=BG)
        tk.Label(page, text="Statistik", bg=BG, fg=TEXT, font=(FONT, 20, "bold")).pack(anchor="w")
        tk.Label(page, text="Stehen = Höhe ab Schwelle (Einstellungen). Wird nur gezählt, solange verbunden.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(page, bg=BG)
        grid.pack(fill="x")
        self.stat_vars = {}
        for c, (key, title) in enumerate((("day", "Heute"), ("week", "Letzte 7 Tage"), ("month", "Letzte 30 Tage"))):
            grid.columnconfigure(c, weight=1)
            box = card(grid)
            box.grid(row=0, column=c, sticky="nsew", padx=(0 if c == 0 else 6, 0 if c == 2 else 6))
            tk.Label(box, text=title, bg=CARD, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
            v = tk.StringVar(value="")
            tk.Label(box, textvariable=v, bg=CARD, fg=TEXT, font=(FONT, 11), justify="left").pack(anchor="w", pady=(6, 0))
            self.stat_vars[key] = v
        chart = card(page)
        chart.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(chart, text="Stehzeit pro Tag (Minuten, letzte 14 Tage)", bg=CARD, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w")
        self.chart = tk.Canvas(chart, bg=CARD, highlightthickness=0, height=220)
        self.chart.pack(fill="both", expand=True, pady=(8, 0))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        make_button(page, "Als CSV exportieren", bg=CARD2, size=10, padx=14, pady=8,
                    command=self.export_csv).pack(anchor="e", pady=(10, 0))
        return page

    def fmt_dur(self, seconds):
        m = int(round(seconds / 60))
        return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"

    def refresh_stats(self):
        self.flush_stats()
        today = date.today()
        for key, days in (("day", 0), ("week", 6), ("month", 29)):
            sit, stand, stands = self.stats.total(today - timedelta(days=days), today)
            self.stat_vars[key].set(f"Stehen: {self.fmt_dur(stand)}\nSitzen: {self.fmt_dur(sit)}\n"
                                    f"Wechsel zum Stehen: {stands}\n"
                                    f"Kalorien (Schätzung): {kcal_for(stand, self.settings['weight_kg']):.0f} kcal")
        self.draw_chart()

    def draw_chart(self):
        c = self.chart
        c.delete("all")
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 120)
        today = date.today()
        days = [today - timedelta(days=i) for i in range(13, -1, -1)]
        data = {r[0]: r[2] for r in self.stats.range(days[0], days[-1])}
        mins = [data.get(d.isoformat(), 0) / 60.0 for d in days]
        top = max(max(mins), 30)
        bw = (w - 40) / len(days)
        for i, (d, m) in enumerate(zip(days, mins)):
            x0 = 20 + i * bw + 4
            bar_h = (h - 40) * (m / top)
            c.create_rectangle(x0, h - 24 - bar_h, x0 + bw - 8, h - 24, fill=ACCENT, outline="")
            c.create_text(x0 + (bw - 8) / 2, h - 10, text=d.strftime("%d.%m."), fill=MUTED, font=(FONT, 8))
            if m >= 1:
                c.create_text(x0 + (bw - 8) / 2, h - 30 - bar_h, text=f"{m:.0f}", fill=TEXT, font=(FONT, 8))

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="tischsteuerung_statistik.csv")
        if not path:
            return
        self.flush_stats()
        rows = self.stats.range(date(2000, 1, 1), date.today())
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Datum", "Sitzen (min)", "Stehen (min)", "Wechsel zum Stehen", "Kalorien (Schätzung)"])
                for day, sit, stand, stands in rows:
                    w.writerow([day, round(sit / 60, 1), round(stand / 60, 1), stands,
                                round(kcal_for(stand, self.settings["weight_kg"]))])
            self.log(f"Statistik exportiert: {path}")
        except OSError as exc:
            messagebox.showerror("Export fehlgeschlagen", str(exc))

    # ---------------------------------------------------------------- Seite: Einstellungen
    def _page_settings(self):
        page = tk.Frame(self.container, bg=BG)
        tk.Label(page, text="Einstellungen", bg=BG, fg=TEXT, font=(FONT, 20, "bold")).pack(anchor="w")
        form = card(page)
        form.pack(fill="x", pady=(10, 0))
        self.form = {}

        def field(row, label, key, hint="", width=8):
            tk.Label(form, text=label, bg=CARD, fg=TEXT, font=(FONT, 10)).grid(row=row, column=0, sticky="w", pady=4)
            v = tk.StringVar()
            tk.Entry(form, textvariable=v, width=width, bg=CARD2, fg=TEXT, insertbackground=TEXT,
                     relief="flat", font=(FONT, 11)).grid(row=row, column=1, sticky="w", padx=10, ipady=3)
            tk.Label(form, text=hint, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=row, column=2, sticky="w")
            self.form[key] = v

        field(0, "Höhe bei Position 0 (Offset, cm)", "offset_cm", "Tischflächenhöhe ganz unten - dann stimmt die Anzeige")
        field(1, "Software-Grenze unten (cm)", "limit_min_cm", "Endlagen des Tisches bleiben unabhängig aktiv")
        field(2, "Software-Grenze oben (cm)", "limit_max_cm")
        for i in range(3):
            field(3 + i, f"Speicher {i + 1}: Höhe (cm)", f"mem{i}")
            field(6 + i, f"Speicher {i + 1}: Name", f"memname{i}", width=14)
        field(9, "Schwelle 'Stehen' ab (cm)", "stand_threshold_cm")
        field(10, "Körpergewicht (kg)", "weight_kg", "für die Kalorienschätzung")
        field(11, "Erinnerung nach Sitzen (min)", "remind_sit_min")
        field(12, "Erinnerung nach Stehen (min)", "remind_stand_min")
        field(13, "Später erinnern nach (min)", "snooze_min")
        self.remind_var = tk.BooleanVar()
        self.oneclick_var = tk.BooleanVar()
        self.hex_var = tk.BooleanVar()
        for r, (text, var) in enumerate((("Erinnerungen aktiv", self.remind_var),
                                         ("Zielfahrt mit einem Klick (aus = nur solange Taste gedrückt, wie im Original)",
                                          self.oneclick_var),
                                         ("USB-Rohdaten im Protokoll anzeigen", self.hex_var))):
            tk.Checkbutton(form, text=text, variable=var, bg=CARD, fg=TEXT, selectcolor=CARD2, activebackground=CARD,
                           activeforeground=TEXT, font=(FONT, 10)).grid(row=14 + r, column=0, columnspan=3, sticky="w")
        make_button(form, "Speichern", bg=ACCENT, size=11, padx=22, pady=8, hover="#2563eb",
                    command=self.save_settings_from_form).grid(row=17, column=0, sticky="w", pady=(12, 0))

        logf = card(page)
        logf.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(logf, text="Protokoll", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w")
        self.log_text = tk.Text(logf, height=6, bg=CARD2, fg=TEXT, relief="flat", font=("Consolas", 9),
                                state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        return page

    def load_settings_into_form(self):
        s = self.settings
        for key in ("offset_cm", "limit_min_cm", "limit_max_cm", "stand_threshold_cm", "weight_kg",
                    "remind_sit_min", "remind_stand_min", "snooze_min"):
            self.form[key].set(str(s[key]))
        for i in range(3):
            self.form[f"mem{i}"].set(str(s["memory"][i]))
            self.form[f"memname{i}"].set(s["memory_names"][i])
        self.remind_var.set(s["remind_enabled"])
        self.oneclick_var.set(s["one_click_goto"])
        self.hex_var.set(s["hex_log"])

    def save_settings_from_form(self):
        s = self.settings
        try:
            f = {k: parse_cm(v.get()) for k, v in self.form.items() if not k.startswith("memname")}
            lo, hi = f["limit_min_cm"], f["limit_max_cm"]
            if lo >= hi:
                raise ValueError("Untere Grenze muss kleiner als obere Grenze sein.")
            if f["weight_kg"] <= 0:
                raise ValueError("Gewicht muss größer als 0 sein.")
        except ValueError as exc:
            messagebox.showerror("Ungültige Eingabe", str(exc) if str(exc) and "could not" not in str(exc)
                                 else "Bitte nur Zahlen eintragen (z. B. 72,5).")
            return
        s["offset_cm"], s["limit_min_cm"], s["limit_max_cm"] = f["offset_cm"], lo, hi
        s["stand_threshold_cm"], s["weight_kg"] = f["stand_threshold_cm"], f["weight_kg"]
        s["remind_sit_min"], s["remind_stand_min"] = int(f["remind_sit_min"]), int(f["remind_stand_min"])
        s["snooze_min"] = max(1, int(f["snooze_min"]))
        s["memory"] = [f[f"mem{i}"] for i in range(3)]
        s["memory_names"] = [self.form[f"memname{i}"].get().strip() or f"Speicher {i + 1}" for i in range(3)]
        s["remind_enabled"], s["one_click_goto"] = self.remind_var.get(), self.oneclick_var.get()
        s["hex_log"] = self.hex_var.get()
        s.save()
        if self.worker:
            self.worker.set_limits(*self.limits_raw())
        self.refresh_memory_labels()
        self.log("Einstellungen gespeichert.")

    def refresh_memory_labels(self):
        for i, (name, val) in enumerate(self.mem_labels):
            name.configure(text=self.settings["memory_names"][i])
            val.configure(text=f"{self.settings['memory'][i]:.1f} cm")
        hint = ("Ein Klick startet die Fahrt. STOPP, Leertaste oder Esc halten an." if self.settings["one_click_goto"]
                else "Taste beim Fahren gedrückt halten - Loslassen stoppt (wie im Original).")
        self.goto_hint.configure(text=hint)

    # ---------------------------------------------------------------- Verbindung
    def toggle_connect(self):
        if self.worker:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        try:
            usb = LinakUSB()
            pos, raw = usb.read_status()
        except Exception as exc:
            self.log(f"VERBINDUNGSFEHLER: {exc}")
            messagebox.showerror("USB-Verbindung fehlgeschlagen",
                                 f"{exc}\n\nPrüfe: Adapter am Bedien-Port (RJ45) der Steuerbox, USB eingesteckt, "
                                 "und keine andere Software (z. B. Desk Control) belegt den Adapter.")
            return
        self.worker = DeskWorker(usb, self.events, self.limits_raw())
        self.worker.pos = pos
        self.worker.start()
        self.conn_btn.configure(text="Trennen")
        self.conn_var.set("Verbunden")
        self.conn_dot.configure(fg=OK)
        self.last_tick = time.monotonic()
        self.log(f"Verbunden. Position {self.raw_to_cm(pos):.1f} cm (roh {pos}).")

    def disconnect(self):
        self.hold_dir, self.held_goto = 0, False
        if self.worker:
            self.worker.shutdown()
            self.worker.join(timeout=2.0)
            self.worker = None
        self.flush_stats()
        self.conn_btn.configure(text="Verbinden")
        self.conn_var.set("Nicht verbunden")
        self.conn_dot.configure(fg=DANGER)
        self.height_var.set("--.-")
        self.sub_var.set("Position (roh): -")
        self.state_var.set("Bereit")
        self.last_tick = None
        self.posture = None
        self.log("Verbindung getrennt.")

    # ---------------------------------------------------------------- Bedienung
    def hold_start(self, direction):
        if not self.worker or self.hold_dir or self.errors_blocking():
            return
        self.hold_dir = direction
        self.worker.hold(direction)
        self._hold_tick()

    def _hold_tick(self):
        if self.worker and self.hold_dir:
            self.worker.hold(self.hold_dir)
            self.after(100, self._hold_tick)

    def hold_end(self):
        if self.hold_dir:
            self.hold_dir = 0
            if self.worker:
                self.worker.stop()

    def errors_blocking(self):
        if any(code == "E01" for code, _ in self.errors):
            self.log("Fahrt blockiert: E01 Position verloren. Bitte Tisch zuerst referenzieren.")
            return True
        return False

    def start_goto(self, target_cm):
        if not self.worker or self.errors_blocking():
            return
        lo, hi = self.settings["limit_min_cm"], self.settings["limit_max_cm"]
        if not lo <= target_cm <= hi:
            messagebox.showerror("Außerhalb der Grenzen", f"{target_cm:.1f} cm liegt außerhalb {lo:.1f} - {hi:.1f} cm.")
            return
        raw = self.cm_to_raw(target_cm)
        if raw < 0:
            messagebox.showerror("Ungültige Höhe", "Die Zielhöhe liegt unter Position 0 (Offset beachten).")
            return
        self.held_goto = not self.settings["one_click_goto"]
        self.log(f"Fahre zu {target_cm:.1f} cm.")
        self.worker.goto(raw, held=self.held_goto)
        if self.held_goto:
            self._goto_tick()

    def _goto_tick(self):
        if self.worker and self.held_goto:
            self.worker.keepalive()
            self.after(100, self._goto_tick)

    def goto_entry(self):
        try:
            cm = parse_cm(self.target_var.get())
        except ValueError:
            messagebox.showerror("Ungültige Eingabe", "Bitte eine Höhe in cm eingeben, z. B. 72,5.")
            return
        self.start_goto(cm)

    def goto_memory(self, i):
        self.start_goto(self.settings["memory"][i])

    def goto_release(self):
        if self.held_goto:
            self.held_goto = False
            if self.worker:
                self.worker.stop()

    def do_stop(self):
        self.hold_dir, self.held_goto = 0, False
        if self.worker:
            self.log("STOPP.")
            self.worker.stop()

    def save_memory(self, i):
        if self.pos is None:
            messagebox.showinfo("Nicht verbunden", "Zum Speichern muss der Tisch verbunden sein.")
            return
        cm = round(self.raw_to_cm(self.pos), 1)
        self.settings["memory"][i] = cm
        self.settings.save()
        self.refresh_memory_labels()
        self.log(f"{self.settings['memory_names'][i]} gespeichert: {cm:.1f} cm.")

    # ---------------------------------------------------------------- Ereignisse / Statistik
    def log(self, msg):
        t = getattr(self, "log_text", None)
        if t is None:
            return
        t.configure(state="normal")
        t.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 400:
            t.delete("1.0", f"{lines - 400}.0")
        t.see("end")
        t.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, *a = self.events.get_nowait()
                if kind == "status":
                    self.on_status(*a)
                elif kind == "tx":
                    if self.settings["hex_log"]:
                        self.log(f"TX {a[0]:#06x}: {a[1][:40]}")
                elif kind == "state":
                    self.state_var.set(a[0])
                    self.state_label.configure(fg=OK if a[0] == "Bereit" else WARN)
                elif kind == "log":
                    self.log(a[0])
                elif kind == "fatal":
                    self.log(f"Verbindung verloren: {a[0]}")
                    self.worker = None
                    self.disconnect()
                    messagebox.showerror("USB-Fehler", f"Verbindung zum Adapter verloren:\n{a[0]}")
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def on_status(self, pos, rawhex, errors):
        self.pos = pos
        cm = self.raw_to_cm(pos)
        self.height_var.set(f"{cm:.1f}")
        self.sub_var.set(f"Position (roh): {pos}")
        if self.settings["hex_log"]:
            self.log(f"RX {rawhex[:60]}")
        if errors != self.errors:
            self.errors = errors
            if errors:
                self.error_var.set("  |  ".join(f"{c}: {t}" for c, t in errors))
                self.error_label.pack(fill="x", padx=24, pady=(10, 0), before=self.container)
                self.log("Fehler: " + self.error_var.get())
            else:
                self.error_label.pack_forget()
        now = time.monotonic()
        if self.last_tick is not None:
            dt = min(now - self.last_tick, 2.0)
            standing = cm >= self.settings["stand_threshold_cm"]
            self.pending["stand" if standing else "sit"] += dt
            posture = "stand" if standing else "sit"
            if posture != self.posture:
                if self.posture == "sit" and posture == "stand":
                    self.pending["stands"] += 1
                self.posture, self.posture_since = posture, now
        self.last_tick = now

    def flush_stats(self):
        p = self.pending
        if p["sit"] or p["stand"] or p["stands"]:
            self.stats.add(self.current_day, p["sit"], p["stand"], p["stands"])
            self.pending = {"sit": 0.0, "stand": 0.0, "stands": 0}
        self.current_day = date.today().isoformat()
        self.last_flush = time.monotonic()

    def _housekeeping(self):
        now = time.monotonic()
        if now - self.last_flush > 30:
            self.flush_stats()
        s = self.settings
        if self.worker and self.posture and s["remind_enabled"] and now > self.snooze_until \
                and not (self.notification and self.notification.winfo_exists()) and self.worker.mode == "idle":
            minutes = (now - self.posture_since) / 60.0
            if self.posture == "sit" and minutes >= s["remind_sit_min"]:
                self.remind("Zeit zum Stehen", f"Du sitzt seit {minutes:.0f} Minuten. Wie wäre es mit einer Runde Stehen?", 1)
            elif self.posture == "stand" and minutes >= s["remind_stand_min"]:
                self.remind("Zeit zum Sitzen", f"Du stehst seit {minutes:.0f} Minuten. Zeit für eine Pause im Sitzen.", 0)
        self.after(1000, self._housekeeping)

    def remind(self, title, message, memory_index):
        def go():
            self.snooze_until = time.monotonic() + 60
            self.goto_memory(memory_index)

        def later():
            self.snooze_until = time.monotonic() + self.settings["snooze_min"] * 60

        def skip():
            self.posture_since = time.monotonic()

        name = self.settings["memory_names"][memory_index]
        self.notification = Notification(self, title, message, [
            (f"{name} anfahren", ACCENT, go), ("Später", CARD2, later), ("Überspringen", CARD2, skip)])

    def on_close(self):
        self.disconnect()
        self.flush_stats()
        self.stats.close()
        self.settings.save()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
