"""INA219 電流モニタ (GUI + ヘッドレス両対応)。

GUI モード (無引数実行、または --gui):
    Tkinter + matplotlib のリアルタイム波形表示。CSV 追記。
    従来通りの挙動。

ヘッドレスモード (--headless):
    serial→CSV loop のみ実行。SIGTERM/SIGINT で clean shutdown。
    screening_run.py がサブプロセス起動して使う。
"""
from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
from collections import deque
from datetime import datetime


def run_headless(port: str, baud: int, csv_path: str) -> int:
    """--headless モード: serial→CSV の最小ループ。"""
    import serial

    stop_requested = {"flag": False}

    def _handle_signal(signum, frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    write_header = not os.path.exists(csv_path)
    ser = serial.Serial(port, baud, timeout=1)

    try:
        with open(csv_path, "a", newline="", buffering=1) as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["Timestamp", "Current (mA)"])
            print(f"[current_monitor headless] port={port} baud={baud} csv={csv_path}", flush=True)
            while not stop_requested["flag"]:
                try:
                    raw = ser.readline().decode(errors="replace").strip()
                except serial.SerialException as e:
                    print(f"[current_monitor headless] serial error: {e}", file=sys.stderr, flush=True)
                    return 3
                if not raw:
                    continue
                try:
                    current_mA = float(raw)
                except ValueError:
                    continue
                writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), current_mA])
    finally:
        ser.close()
        print("[current_monitor headless] closed", flush=True)
    return 0


def run_gui() -> int:
    """GUI モード (Tkinter)。"""
    import serial
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import tkinter as tk
    from tkinter import ttk, messagebox

    class CurrentMonitorGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Current Monitor")
            self.root.geometry("800x600")

            self.ser = None
            self.data = deque(maxlen=100)
            self.ani = None
            self.is_monitoring = False

            self.create_widgets()

        def create_widgets(self):
            settings_frame = ttk.LabelFrame(self.root, text="Settings", padding="10")
            settings_frame.pack(fill="x", padx=10, pady=5)

            ttk.Label(settings_frame, text="Serial Port:").grid(row=0, column=0, sticky="w")
            self.port_var = tk.StringVar(value="COM12")
            ttk.Entry(settings_frame, textvariable=self.port_var, width=15).grid(row=0, column=1, padx=5)

            ttk.Label(settings_frame, text="Baud Rate:").grid(row=0, column=2, sticky="w", padx=(20, 0))
            self.baud_var = tk.StringVar(value="9600")
            ttk.Entry(settings_frame, textvariable=self.baud_var, width=10).grid(row=0, column=3, padx=5)

            ttk.Label(settings_frame, text="CSV Filename:").grid(row=1, column=0, sticky="w")
            self.csv_var = tk.StringVar(value="current_log.csv")
            ttk.Entry(settings_frame, textvariable=self.csv_var, width=30).grid(row=1, column=1, columnspan=2, padx=5, pady=5)

            self.start_button = ttk.Button(settings_frame, text="Start", command=self.start_monitoring)
            self.start_button.grid(row=1, column=3, padx=5, pady=5)

            self.stop_button = ttk.Button(settings_frame, text="Stop", command=self.stop_monitoring, state="disabled")
            self.stop_button.grid(row=1, column=4, padx=5, pady=5)

            graph_frame = ttk.Frame(self.root)
            graph_frame.pack(fill="both", expand=True, padx=10, pady=5)

            self.fig, self.ax = plt.subplots(figsize=(10, 6))
            self.line, = self.ax.plot([], [], lw=2)
            self.ax.set_ylim(0, 200)
            self.ax.set_xlim(0, 100)
            self.ax.set_title("Real-time Current (mA)")
            self.ax.set_ylabel("Current [mA]")
            self.ax.set_xlabel("Sample")

            self.canvas = FigureCanvasTkAgg(self.fig, graph_frame)
            self.canvas.draw()
            self.canvas.get_tk_widget().pack(fill="both", expand=True)

        def start_monitoring(self):
            try:
                port = self.port_var.get()
                baud = int(self.baud_var.get())
                self.csv_filename = self.csv_var.get()

                self.ser = serial.Serial(port, baud, timeout=1)

                if not os.path.exists(self.csv_filename):
                    with open(self.csv_filename, "w", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(["Timestamp", "Current (mA)"])

                self.data.clear()

                self.ani = animation.FuncAnimation(
                    self.fig, self.update_plot, init_func=self.init_plot,
                    interval=25, blit=True
                )

                self.is_monitoring = True
                self.start_button.config(state="disabled")
                self.stop_button.config(state="normal")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to start monitoring: {str(e)}")

        def stop_monitoring(self):
            self.is_monitoring = False
            if self.ani:
                self.ani.event_source.stop()
            if self.ser:
                self.ser.close()

            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")

        def init_plot(self):
            self.line.set_data([], [])
            return self.line,

        def update_plot(self, frame):
            if not self.is_monitoring or not self.ser:
                return self.line,

            while self.ser.in_waiting > 1:
                self.ser.readline()

            try:
                raw = self.ser.readline().decode().strip()
                current_mA = float(raw)
                self.data.append(current_mA)
                self.line.set_data(range(len(self.data)), list(self.data))

                with open(self.csv_filename, "a", newline="") as f:
                    writer = csv.writer(f)
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
                    writer.writerow([timestamp, current_mA])

            except ValueError:
                pass

            return self.line,

    root = tk.Tk()
    app = CurrentMonitorGUI(root)
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="current_monitor",
        description="INA219 電流モニタ (GUI / ヘッドレス両対応)",
    )
    p.add_argument("--headless", action="store_true", help="ヘッドレスモード (serial→CSV のみ)")
    p.add_argument("--port", default="COM12", help="シリアルポート (default: COM12)")
    p.add_argument("--baud", type=int, default=9600, help="ボーレート (default: 9600)")
    p.add_argument("--csv", default="current_log.csv", help="CSV 出力パス (default: current_log.csv)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.headless:
        return run_headless(args.port, args.baud, args.csv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
