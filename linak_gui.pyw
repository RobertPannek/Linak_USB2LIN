import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

VID = 0x12D3
PID = 0x0002
REPORT_LEN = 64
MIN_POSITION = 0
MAX_POSITION = 6480  # Reference-project example; actual desk limits may differ.
STEP = 80             # Small target increments while a button is held.
UPDATE_SECONDS = 0.25

class LinakUSB:
    """USB2LIN06 access using HID feature reports; based on public USB2LIN06 references."""
    def __init__(self):
        try:
            import hid
        except ImportError as exc:
            raise RuntimeError("Das Paket 'hid' fehlt. Bitte installiere die Abhängigkeiten über install.bat.") from exc
        self.hid_module = hid
        self.dev = hid.device()
        self.dev.open(VID, PID)
        self.dev.set_nonblocking(0)
        # Set operating mode (HID feature report 3). Report ID is byte 0.
        init = bytearray(REPORT_LEN)
        init[0] = 3
        init[1] = 4
        init[2] = 0
        init[3] = 0xFB
        self.dev.send_feature_report(bytes(init))
        time.sleep(0.5)

    def get_position(self):
        # Request status feature report (report ID 4).
        raw = self.dev.get_feature_report(4, REPORT_LEN)
        if not raw or len(raw) < 7:
            raise RuntimeError(f"Ungültige Statusantwort: {raw!r}")
        position = int(raw[4]) + (int(raw[5]) << 8)
        moving = int(raw[6]) > 0
        return position, moving, bytes(raw)

    def move_to(self, position):
        position = max(MIN_POSITION, min(MAX_POSITION, int(position)))
        report = bytearray(REPORT_LEN)
        report[0] = 5
        lo = position & 0xFF
        hi = (position >> 8) & 0xFF
        # Byte placement follows the public gryf/linak-ctrl reference implementation.
        report[1] = report[3] = report[5] = report[7] = lo
        report[2] = report[4] = report[6] = report[8] = hi
        self.dev.send_feature_report(bytes(report))
        return bytes(report)

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LINAK USB2LIN06 – Windows 11")
        self.geometry("760x600")
        self.minsize(700, 520)
        self.usb = None
        self.running = False
        self.direction = 0
        self.worker = None
        self.last_position = None
        self.after_id = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Bereit. Adapter anschließen und 'Verbinden' drücken.")
        self.log("Sicherheit: freie Bewegungsbahn sicherstellen und jederzeit Netzstrom/Bedienelement erreichbar halten.")

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="LINAK USB2LIN06", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Windows 11 · CBD6S · VID 12D3 / PID 0002").pack(anchor="w", pady=(0, 12))

        top = ttk.Frame(root)
        top.pack(fill="x")
        self.connect_btn = ttk.Button(top, text="Verbinden", command=self.connect)
        self.connect_btn.pack(side="left")
        self.disconnect_btn = ttk.Button(top, text="Trennen", command=self.disconnect, state="disabled")
        self.disconnect_btn.pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="Nicht verbunden")
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=10)

        position_frame = ttk.LabelFrame(root, text="Status", padding=12)
        position_frame.pack(fill="x", pady=12)
        self.height_var = tk.StringVar(value="—")
        self.moving_var = tk.StringVar(value="Bewegung: unbekannt")
        ttk.Label(position_frame, textvariable=self.height_var, font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(position_frame, textvariable=self.moving_var).pack(anchor="w")

        controls = ttk.LabelFrame(root, text="Bedienung (Taste gedrückt halten)", padding=12)
        controls.pack(fill="x")
        self.up_btn = ttk.Button(controls, text="▲  HOCH HALTEN", command=lambda: None)
        self.up_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=14)
        self.down_btn = ttk.Button(controls, text="▼  RUNTER HALTEN", command=lambda: None)
        self.down_btn.grid(row=0, column=1, sticky="ew", ipady=14)
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        for btn, direction in ((self.up_btn, 1), (self.down_btn, -1)):
            btn.bind("<ButtonPress-1>", lambda e, d=direction: self.start_motion(d))
            btn.bind("<ButtonRelease-1>", self.stop_motion)
            btn.bind("<Leave>", self.stop_motion)
            btn.bind("<FocusOut>", self.stop_motion)
        self.up_btn.state(["disabled"])
        self.down_btn.state(["disabled"])

        warning = ttk.Label(root, text="Hinweis: Diese erste Version nutzt kleine Positionsziele, um eine Bewegung bei gedrückter Taste anzufordern. Das Verhalten muss am eigenen Tisch vorsichtig geprüft werden. Keine Sicherheits- oder Kollisionsfunktionen umgehen.", wraplength=700)
        warning.pack(anchor="w", pady=10)

        log_frame = ttk.LabelFrame(root, text="Diagnose / Hex-Protokoll", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.log_text = tk.Text(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def log(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def connect(self):
        if self.usb:
            return
        try:
            self.usb = LinakUSB()
            pos, moving, raw = self.usb.get_position()
            self.last_position = pos
            self.status_var.set("Verbunden")
            self.connect_btn.state(["disabled"])
            self.disconnect_btn.state(["!disabled"])
            self.up_btn.state(["!disabled"])
            self.down_btn.state(["!disabled"])
            self.log(f"Verbunden: VID=0x{VID:04X}, PID=0x{PID:04X}")
            self.log(f"Status RX: {raw.hex(' ').upper()}")
            self.update_status()
        except Exception as exc:
            if self.usb:
                self.usb.close()
                self.usb = None
            self.log(f"VERBINDUNGSFEHLER: {exc}")
            messagebox.showerror("USB-Verbindung fehlgeschlagen", str(exc) + "\n\nPrüfe, ob der Adapter erkannt wird und ob eine andere LINAK-Anwendung ihn gerade verwendet.")

    def update_status(self):
        if not self.usb:
            return
        try:
            pos, moving, raw = self.usb.get_position()
            self.last_position = pos
            self.height_var.set(f"Position: {pos} (Rohwert; keine kalibrierte cm-Anzeige)")
            self.moving_var.set("Bewegung: ja" if moving else "Bewegung: nein")
            self.log(f"Status RX: {raw.hex(' ').upper()}")
        except Exception as exc:
            self.log(f"Statusfehler: {exc}")
        self.after_id = self.after(600, self.update_status)

    def start_motion(self, direction):
        if not self.usb or self.direction:
            return
        self.direction = direction
        self.log("Taste gedrückt: " + ("HOCH" if direction > 0 else "RUNTER"))
        self.worker = threading.Thread(target=self.motion_loop, daemon=True)
        self.worker.start()

    def motion_loop(self):
        while self.direction and self.usb:
            try:
                pos, moving, _ = self.usb.get_position()
                target = max(MIN_POSITION, min(MAX_POSITION, pos + self.direction * STEP))
                if target == pos:
                    self.log_threadsafe("Positionsgrenze erreicht; keine weitere Anforderung.")
                    break
                sent = self.usb.move_to(target)
                self.log_threadsafe(f"TX MOVE Ziel={target}: {sent.hex(' ').upper()}")
                time.sleep(UPDATE_SECONDS)
            except Exception as exc:
                self.log_threadsafe(f"Bewegungsfehler: {exc}")
                break
        self.direction = 0

    def log_threadsafe(self, msg):
        self.after(0, lambda m=msg: self.log(m))

    def stop_motion(self, event=None):
        if not self.direction:
            return
        self.direction = 0
        self.log("Taste losgelassen: Stopp-Anforderung wird gesendet.")
        # Retarget to the current reported position; this is not a certified emergency stop.
        if self.usb:
            threading.Thread(target=self.stop_at_current_position, daemon=True).start()

    def stop_at_current_position(self):
        try:
            pos, _, _ = self.usb.get_position()
            sent = self.usb.move_to(pos)
            self.log_threadsafe(f"TX STOP-ZIEL={pos}: {sent.hex(' ').upper()}")
        except Exception as exc:
            self.log_threadsafe(f"Stopp-Anforderung fehlgeschlagen: {exc}")

    def disconnect(self):
        self.stop_motion()
        if self.after_id:
            try: self.after_cancel(self.after_id)
            except Exception: pass
            self.after_id = None
        if self.usb:
            self.usb.close()
            self.usb = None
        self.status_var.set("Nicht verbunden")
        self.height_var.set("—")
        self.moving_var.set("Bewegung: unbekannt")
        self.connect_btn.state(["!disabled"])
        self.disconnect_btn.state(["disabled"])
        self.up_btn.state(["disabled"])
        self.down_btn.state(["disabled"])
        self.log("Verbindung getrennt.")

    def on_close(self):
        self.stop_motion()
        self.disconnect()
        self.destroy()

if __name__ == "__main__":
    App().mainloop()
