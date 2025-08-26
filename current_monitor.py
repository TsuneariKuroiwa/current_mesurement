import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import deque
from datetime import datetime
import csv
import os
import tkinter as tk
from tkinter import ttk, messagebox

class CurrentMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Current Monitor")
        self.root.geometry("800x600")
        
        # 変数初期化
        self.ser = None
        self.data = deque(maxlen=100)
        self.ani = None
        self.is_monitoring = False
        
        # GUI要素作成
        self.create_widgets()
        
    def create_widgets(self):
        # 設定フレーム
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding="10")
        settings_frame.pack(fill="x", padx=10, pady=5)
        
        # Serial Port
        ttk.Label(settings_frame, text="Serial Port:").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar(value="COM12")
        ttk.Entry(settings_frame, textvariable=self.port_var, width=15).grid(row=0, column=1, padx=5)
        
        # Baud Rate
        ttk.Label(settings_frame, text="Baud Rate:").grid(row=0, column=2, sticky="w", padx=(20,0))
        self.baud_var = tk.StringVar(value="9600")
        ttk.Entry(settings_frame, textvariable=self.baud_var, width=10).grid(row=0, column=3, padx=5)
        
        # CSV Filename
        ttk.Label(settings_frame, text="CSV Filename:").grid(row=1, column=0, sticky="w")
        self.csv_var = tk.StringVar(value="current_log.csv")
        ttk.Entry(settings_frame, textvariable=self.csv_var, width=30).grid(row=1, column=1, columnspan=2, padx=5, pady=5)
        
        # 制御ボタン
        self.start_button = ttk.Button(settings_frame, text="Start", command=self.start_monitoring)
        self.start_button.grid(row=1, column=3, padx=5, pady=5)
        
        self.stop_button = ttk.Button(settings_frame, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.grid(row=1, column=4, padx=5, pady=5)
        
        # グラフフレーム
        graph_frame = ttk.Frame(self.root)
        graph_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # matplotlib図作成
        self.fig, self.ax = plt.subplots(figsize=(10, 6))
        self.line, = self.ax.plot([], [], lw=2)
        self.ax.set_ylim(0, 200)
        self.ax.set_xlim(0, 100)
        self.ax.set_title("Real-time Current (mA)")
        self.ax.set_ylabel("Current [mA]")
        self.ax.set_xlabel("Sample")
        
        # tkinterにmatplotlibを埋め込み
        self.canvas = FigureCanvasTkAgg(self.fig, graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        
    def start_monitoring(self):
        try:
            # シリアル接続
            port = self.port_var.get()
            baud = int(self.baud_var.get())
            self.csv_filename = self.csv_var.get()
            
            self.ser = serial.Serial(port, baud, timeout=1)
            
            # CSVファイル初期化
            if not os.path.exists(self.csv_filename):
                with open(self.csv_filename, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Timestamp', 'Current (mA)'])
            
            # データリセット
            self.data.clear()
            
            # アニメーション開始
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
            
        # 溜まっていた古いデータを捨てて最新に追いつく
        while self.ser.in_waiting > 1:
            self.ser.readline()

        try:
            raw = self.ser.readline().decode().strip()
            current_mA = float(raw)
            self.data.append(current_mA)
            self.line.set_data(range(len(self.data)), list(self.data))

            # CSV保存
            with open(self.csv_filename, 'a', newline='') as f:
                writer = csv.writer(f)
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                writer.writerow([timestamp, current_mA])

        except ValueError:
            pass

        return self.line,

# メイン実行部分
if __name__ == "__main__":
    root = tk.Tk()
    app = CurrentMonitorGUI(root)
    root.mainloop()


