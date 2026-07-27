"""screening 共通ユーティリティ。CSV → 特徴量抽出。

`current_monitor.py --headless` が吐く CSV (Timestamp, Current (mA)) を読み、
device1 型 (+20-30 mA 余分な常時消費) 異常個体の検出に有用な特徴量を出す。

抽出方針は「フェーズ細分化を避けたシンプル閾値ベース」:
- baseline: 電流モニタ開始直後の低電流 window (nRF 全 OFF 期待 ~0.2 mA)
- global: 全区間の mean/median/max/std
- wifi_on: 電流 > 30 mA のサンプル (ウミネコ実測で WiFi ON 区間電流 106-167 mA)
- high_current: 電流 > 100 mA のサンプル (カメラ + WiFi ピーク)

screening_analyze.py / screening_overview.py の両方から使う。
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


FQBN = "adafruit:nrf52:feather52840"
LOGBOT_CTL_PATH = r"C:\Users\tsune\logbot\tools\logbot-ctl\logbot_ctl.py"

# Adafruit Feather nRF52840 の VID/PID
ADAFRUIT_FEATHER_VID = 0x239A
ADAFRUIT_FEATHER_APP_PID = 0x8029     # 通常スケッチ動作時
# Bootloader モード時の PID (Adafruit の bootloader 世代/SoftDevice 版で異なる)
# 0x0029: nRF52832 系 Bluefruit LE Bootloader
# 0x002A: nRF52840 系 Bluefruit LE Bootloader (Feather nRF52840 Express はこちら)
ADAFRUIT_BOOTLOADER_PIDS = (0x0029, 0x002A)

# Adafruit-nrfutil DFU 失敗マーカー (exit code 0 を返すバグ気味挙動を stderr/stdout で検出)
_DFU_FAILURE_MARKERS = (
    "Timed out waiting for acknowledgement",
    "Target is not in DFU mode",
    "PortNotOpenError",
    "could not open port",
)


def find_dfu_port(timeout_sec: float = 5.0, poll_interval_s: float = 0.5) -> str | None:
    """DFU モードの Adafruit Feather nRF52840 の COM ポートを探す。

    DFU モード遷移時は VID/PID が 0x239A/0x0029 (bootloader) になり、
    COM 番号が通常時と別に割り当てられる (Windows CDC 二重列挙)。
    """
    from serial.tools import list_ports
    end = time.monotonic() + timeout_sec
    while time.monotonic() < end:
        for p in list_ports.comports():
            if p.vid == ADAFRUIT_FEATHER_VID and p.pid in ADAFRUIT_BOOTLOADER_PIDS:
                return p.device
        time.sleep(poll_interval_s)
    return None


def force_dfu_via_logbot_ctl(if_port: str) -> bool:
    """logbot-ctl reset --double で nRF52 を hardware reset → bootloader (DFU) モードに強制遷移。

    1200 baud reset に応答しない firmware (初期プログラム等) 用の fallback。
    """
    import sys as _sys
    import subprocess as _sp
    cmd = [_sys.executable, LOGBOT_CTL_PATH, "reset", if_port, "--double"]
    try:
        _sp.run(cmd, check=True, capture_output=True, text=True, timeout=10)
        return True
    except (_sp.CalledProcessError, _sp.TimeoutExpired):
        return False


def trigger_bootloader_1200_baud(v5_port: str, hold_sec: float = 1.0) -> bool:
    """v5_port を 1200 baud で open → hold → close して Adafruit Feather を bootloader へ。

    Adafruit TinyUSB の 1200 baud detection は close イベントで発火するが、open 直後の
    close だと baudrate 通知前に close される場合ある。hold_sec で SET_LINE_CODING が
    device 側で確実に処理される時間を確保する。

    v5_port が既に存在しない (V5 が既に DFU モードの場合) は False を返す。
    """
    import serial
    import time as _time
    try:
        sp = serial.Serial(v5_port, 1200, timeout=1)
        _time.sleep(hold_sec)
        sp.close()
        return True
    except serial.SerialException:
        return False


def compile_and_upload_v5_with_fallback(sketch_dir: Path, v5_port: str,
                                        if_port: str | None = None,
                                        dfu_wait_sec: float = 30.0) -> tuple[bool, dict]:
    """compile_and_upload_v5 の 1200 baud reset が効かない場合、
    logbot-ctl reset --double で hardware DFU 強制遷移 → retry する。

    if_port 未指定時は fallback なし。
    """
    import time as _time
    ok, info = compile_and_upload_v5(sketch_dir, v5_port, dfu_wait_sec=dfu_wait_sec)
    if ok or if_port is None:
        return ok, info
    # 1200 baud reset が効かなかった (1200 baud reset に応答しない firmware) 場合の fallback
    info.setdefault("fallback_attempts", []).append({
        "attempt": 1,
        "failure_marker": info.get("failure_marker"),
    })
    print(f"[fallback] compile_and_upload_v5 失敗 → logbot-ctl reset --double 経由で retry",
          flush=True)
    if not force_dfu_via_logbot_ctl(if_port):
        info["fallback_attempts"].append({"attempt": 2, "error": "force_dfu_via_logbot_ctl failed"})
        return False, info
    _time.sleep(3.0)
    ok2, info2 = compile_and_upload_v5(sketch_dir, v5_port, dfu_wait_sec=dfu_wait_sec)
    info2["fallback_attempts"] = info["fallback_attempts"]
    return ok2, info2


def compile_and_upload_v5(sketch_dir: Path, v5_port: str,
                          dfu_wait_sec: float = 30.0) -> tuple[bool, dict]:
    """arduino-cli compile -u で V5 に書き込み。

    Adafruit Feather nRF52840 の書き込みシーケンス:
      1. v5_port を 1200 baud で open/close → bootloader trigger (明示的に)
         (arduino-cli の内部 1200 baud reset は Windows CDC 遷移タイミングで
          タイムアウトすることがあるため、こちら側で確実に発火させる)
      2. find_dfu_port で bootloader COM (PID=0x0029) を待つ
      3. arduino-cli --port <DFU_COM> で書き込み
    既に V5 が DFU モードの場合 (前回書き込み中断等): 1200 baud trigger 不要、
    find_dfu_port が即座に発見して arduino-cli に渡す。

    adafruit-nrfutil は DFU 失敗しても exit 0 で返すバグ気味挙動があるため、
    stderr/stdout の DFU 失敗マーカーもチェックする。

    Returns:
        (success, info_dict)
    """
    # Step 1: 既に DFU モードなら skip、そうでなければ 1200 baud trigger
    dfu_port = find_dfu_port(timeout_sec=0.5)
    triggered = False
    if not dfu_port:
        triggered = trigger_bootloader_1200_baud(v5_port)
        # bootloader 遷移待ち
        dfu_port = find_dfu_port(timeout_sec=dfu_wait_sec)

    upload_port = dfu_port or v5_port
    cmd = ["arduino-cli", "compile", "--fqbn", FQBN, "-u", "-p", upload_port, str(sketch_dir)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    stdout = r.stdout or ""
    stderr = r.stderr or ""
    combined = stdout + "\n" + stderr
    marker_hit = next((m for m in _DFU_FAILURE_MARKERS if m in combined), None)
    success = (r.returncode == 0) and (marker_hit is None)
    return success, {
        "cmd": " ".join(cmd),
        "v5_port_arg": v5_port,
        "trigger_1200_baud": triggered,
        "dfu_port_detected": dfu_port,
        "upload_port_used": upload_port,
        "returncode": r.returncode,
        "success": success,
        "failure_marker": marker_hit,
        "stdout_tail": stdout.splitlines()[-15:],
        "stderr_tail": stderr.splitlines()[-15:],
    }


SAMPLE_INTERVAL_S = 0.025       # current_measurement.ino が 25 ms 周期で吐く
# baseline window: reset 起点で以下のタイムラインを想定
#   0-1s  V5 boot
#   1-4s  setup() delay(3s)
#   4-6s  loop() 冒頭の phase_marker() spike (500ms CPU busyloop x 3 = ~1.5s)
#   6-24s phase_wait_power() の delay 5s x 3 + interval 1s x 3 = 18s (baseline = ~0 mA)
#   24-25.5s 次の phase_marker() spike
# monitor 起動は reset の 3 秒前なので t_sec に +3 秒のオフセット
# → 20-25 秒 window は Phase 1 delay の中央部分 (marker 除外) にほぼ確実にヒット
BASELINE_START_S = 20.0
BASELINE_END_S = 25.0
BASELINE_WINDOW_S = BASELINE_END_S - BASELINE_START_S  # 後方互換
WIFI_ON_THRESHOLD_MA = 30.0
HIGH_CURRENT_THRESHOLD_MA = 100.0


@dataclass
class Features:
    """1 CSV からの抽出特徴量。ペア横断比較で outlier 判定に使う。

    Phase 別統計 (wifi_*, camera_*, sd_*) は burst duration で自動分類:
      - WiFi:   burst 3-10 秒 & mean > 100 mA (5 秒 スキャン × 3 rep)
      - Camera: burst 15-30 秒 & mean > 100 mA (20 秒 録画 × 2 rep)
      - SD:     burst < 2 秒 & 30 mA < mean ≤ 100 mA (Pattern B 一括書き込み × 3 rep)
    """
    pair_id: int
    csv_path: str
    sample_count: int
    duration_s: float
    baseline_median_mA: float
    baseline_p95_mA: float
    global_mean_mA: float
    global_median_mA: float
    global_max_mA: float
    global_std_mA: float
    # 旧: 電流 > 30 mA / > 100 mA の閾値ベース (Phase 混在、参考値)
    wifi_on_mean_mA: float | None
    wifi_on_duration_s: float
    high_current_mean_mA: float | None
    high_current_duration_s: float
    # Phase 別統計 (burst 分類)
    wifi_burst_count: int
    wifi_mean_mA: float | None
    wifi_max_mA: float | None
    wifi_total_dur_s: float
    camera_burst_count: int
    camera_mean_mA: float | None
    camera_max_mA: float | None
    camera_total_dur_s: float
    sd_burst_count: int
    sd_mean_mA: float | None
    sd_max_mA: float | None
    sd_total_dur_s: float
    dropout_ratio: float  # 25 ms 期待に対する欠損率

    def to_dict(self) -> dict:
        return asdict(self)


def load_csv(csv_path: str | Path) -> pd.DataFrame:
    """CSV を DataFrame に。t_sec 列 (経過秒) と I_mA 列を追加。"""
    df = pd.read_csv(csv_path)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    t0 = df["Timestamp"].iloc[0]
    df["t_sec"] = (df["Timestamp"] - t0).dt.total_seconds()
    df["I_mA"] = df["Current (mA)"]
    return df


def _detect_bursts(df: pd.DataFrame, threshold_mA: float) -> pd.DataFrame:
    """電流 > threshold の連続区間 (burst) を検出して DataFrame 返す。

    Returns: columns = [start, end, dur_s, mean_mA, max_mA, n]
    """
    is_high = df["I_mA"] > threshold_mA
    if not is_high.any():
        return pd.DataFrame(columns=["start", "end", "dur_s", "mean_mA", "max_mA", "n"])
    group = (is_high != is_high.shift()).cumsum()
    bursts = df[is_high].groupby(group[is_high]).agg(
        start=("t_sec", "min"),
        end=("t_sec", "max"),
        mean_mA=("I_mA", "mean"),
        max_mA=("I_mA", "max"),
        n=("t_sec", "size"),
    )
    bursts["dur_s"] = bursts["end"] - bursts["start"]
    return bursts.reset_index(drop=True)


def _classify_bursts(bursts: pd.DataFrame) -> dict:
    """burst duration + mean で WiFi / Camera / SD に分類して統計。"""
    def _agg(sub: pd.DataFrame) -> dict:
        if sub.empty:
            return {"count": 0, "mean_mA": None, "max_mA": None, "total_dur_s": 0.0}
        return {
            "count": int(len(sub)),
            "mean_mA": float((sub["mean_mA"] * sub["n"]).sum() / sub["n"].sum()),
            "max_mA": float(sub["max_mA"].max()),
            "total_dur_s": float(sub["dur_s"].sum()),
        }

    # SD burst: 短時間 (< 2s) & 中程度電流 (30-100 mA)
    sd = bursts[(bursts["dur_s"] < 2.0)
                & (bursts["mean_mA"] > 30.0) & (bursts["mean_mA"] <= 100.0)]
    # WiFi burst: 3-10s & 100+ mA (5s スキャン × 3、boot + scan 含む)
    wifi = bursts[(bursts["dur_s"] >= 3.0) & (bursts["dur_s"] < 10.0)
                  & (bursts["mean_mA"] > 100.0)]
    # Camera burst: 15-30s & 100+ mA (20s 録画 × 2)
    camera = bursts[(bursts["dur_s"] >= 15.0) & (bursts["dur_s"] < 30.0)
                    & (bursts["mean_mA"] > 100.0)]
    return {"wifi": _agg(wifi), "camera": _agg(camera), "sd": _agg(sd)}


def compute_features(df: pd.DataFrame, pair_id: int, csv_path: str) -> Features:
    """DataFrame から特徴量を抽出。"""
    duration = float(df["t_sec"].iloc[-1]) if len(df) else 0.0

    baseline_win = df[(df["t_sec"] >= BASELINE_START_S)
                       & (df["t_sec"] < BASELINE_END_S)]["I_mA"]
    baseline_median = float(baseline_win.median()) if not baseline_win.empty else float("nan")
    baseline_p95 = float(baseline_win.quantile(0.95)) if not baseline_win.empty else float("nan")

    global_mean = float(df["I_mA"].mean())
    global_median = float(df["I_mA"].median())
    global_max = float(df["I_mA"].max())
    global_std = float(df["I_mA"].std())

    wifi_on = df[df["I_mA"] > WIFI_ON_THRESHOLD_MA]["I_mA"]
    wifi_on_mean = float(wifi_on.mean()) if not wifi_on.empty else None
    wifi_on_duration = len(wifi_on) * SAMPLE_INTERVAL_S

    high = df[df["I_mA"] > HIGH_CURRENT_THRESHOLD_MA]["I_mA"]
    high_mean = float(high.mean()) if not high.empty else None
    high_duration = len(high) * SAMPLE_INTERVAL_S

    # burst 検出 → WiFi / Camera / SD 分類
    bursts = _detect_bursts(df, threshold_mA=WIFI_ON_THRESHOLD_MA)
    ph = _classify_bursts(bursts)

    expected_samples = duration / SAMPLE_INTERVAL_S if duration > 0 else 1.0
    dropout = max(0.0, 1.0 - len(df) / expected_samples)

    return Features(
        pair_id=pair_id,
        csv_path=str(csv_path),
        sample_count=len(df),
        duration_s=duration,
        baseline_median_mA=baseline_median,
        baseline_p95_mA=baseline_p95,
        global_mean_mA=global_mean,
        global_median_mA=global_median,
        global_max_mA=global_max,
        global_std_mA=global_std,
        wifi_on_mean_mA=wifi_on_mean,
        wifi_on_duration_s=wifi_on_duration,
        high_current_mean_mA=high_mean,
        high_current_duration_s=high_duration,
        wifi_burst_count=ph["wifi"]["count"],
        wifi_mean_mA=ph["wifi"]["mean_mA"],
        wifi_max_mA=ph["wifi"]["max_mA"],
        wifi_total_dur_s=ph["wifi"]["total_dur_s"],
        camera_burst_count=ph["camera"]["count"],
        camera_mean_mA=ph["camera"]["mean_mA"],
        camera_max_mA=ph["camera"]["max_mA"],
        camera_total_dur_s=ph["camera"]["total_dur_s"],
        sd_burst_count=ph["sd"]["count"],
        sd_mean_mA=ph["sd"]["mean_mA"],
        sd_max_mA=ph["sd"]["max_mA"],
        sd_total_dur_s=ph["sd"]["total_dur_s"],
        dropout_ratio=dropout,
    )


def outlier_verdict(features: Features) -> tuple[bool, list[str]]:
    """1 ペア単体で判定できる absolute な異常条件。

    12 ペア横断の相対 outlier (IQR × 1.5) は overview 側で判定するので、ここは絶対閾値のみ。

    device1 型異常 (+20-30 mA 常時消費) の主判定は baseline_median。baseline_p95 は phase_marker
    の CPU spike (~5-10 mA) を拾いやすいため主判定から外し、大きな異常のみ (> 20 mA) をフラグ。
    """
    reasons: list[str] = []
    if features.baseline_median_mA > 1.0:
        reasons.append(
            f"baseline_median={features.baseline_median_mA:.2f} mA > 1.0 (device1 型異常の疑い)"
        )
    if features.baseline_p95_mA > 20.0:
        reasons.append(
            f"baseline_p95={features.baseline_p95_mA:.2f} mA > 20 (baseline に大きな異常 spike)"
        )
    if features.dropout_ratio > 0.05:
        reasons.append(
            f"dropout_ratio={features.dropout_ratio:.1%} > 5% (電流モニタ欠損)"
        )
    if features.wifi_on_mean_mA is not None and features.wifi_on_mean_mA > 250.0:
        reasons.append(
            f"wifi_on_mean={features.wifi_on_mean_mA:.1f} mA > 250 (ウミネコ実測 106-167 mA を大幅超過)"
        )
    return len(reasons) > 0, reasons


def load_all_features(data_dir: Path) -> list[Features]:
    """data_dir 配下の全 pair の meta.json + features.json を読んで list に。"""
    features: list[Features] = []
    for meta_path in sorted(data_dir.glob("screening_pair*_meta.json")):
        summary_path = meta_path.with_name(meta_path.name.replace("_meta.json", "_features.json"))
        if not summary_path.exists():
            continue
        with open(summary_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        features.append(Features(**d))
    return features
