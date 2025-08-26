import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
from datetime import datetime
import csv
import os

# シリアル設定（環境に合わせて変更）
SERIAL_PORT = 'COM12'   # 例：'COM3'（Windows）または '/dev/ttyUSB0'（Linux/Mac）
BAUD_RATE = 9600
MAX_DATA_POINTS = 100
CSV_FILENAME = 'current_log.csv'

# シリアルポート接続（タイムアウト付き）
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)

# CSVファイルを作成（初回のみヘッダー付き）
if not os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Current (mA)'])

# グラフ設定
data = deque(maxlen=MAX_DATA_POINTS)

fig, ax = plt.subplots()
line, = ax.plot([], [], lw=2)
ax.set_ylim(0, 200)  # mAの範囲に応じて調整（例：0〜500mA）
ax.set_xlim(0, MAX_DATA_POINTS)
ax.set_title("Real-time Current (mA)")
ax.set_ylabel("Current [mA]")
ax.set_xlabel("Sample")

def init():
    line.set_data([], [])
    return line,

def update(frame):
    # 溜まっていた古いデータを捨てて最新に追いつく
    while ser.in_waiting > 1:
        ser.readline()

    try:
        raw = ser.readline().decode().strip()
        current_mA = float(raw)
        data.append(current_mA)
        line.set_data(range(len(data)), list(data))

        # CSV保存
        with open(CSV_FILENAME, 'a', newline='') as f:
            writer = csv.writer(f)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            writer.writerow([timestamp, current_mA])

    except ValueError:
        pass

    return line,


ani = animation.FuncAnimation(fig, update, init_func=init, interval=25, blit=True)
plt.tight_layout()
plt.show()
