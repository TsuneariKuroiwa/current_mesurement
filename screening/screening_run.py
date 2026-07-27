"""1 ペア分の電流測定オーケストレータ (対話ラリー方式)。

前提: user が pair N を組んで USB 接続を済ませ、Claude が本スクリプトを呼び出す。

順序 (大原則: `logbot-usb off` の後は V5 と通信不能。`logbot-ctl` は VBUS ON 中のみ、
`logbot-usb off` は `reset` の直後に置く):

  1. logbot-usb on           (VBUS ON 明示、V5 は PC 給電で通信可)
  2. logbot-ctl info         (F/W 版と Board を記録)
  3. logbot-ctl tm           (RTC 同期)
  4. current_monitor.py --headless & (電流ログ開始)
  5. sleep 3s                (baseline 記録)
  6. logbot-ctl reset --double (DUT リセット → nrf_power_test_all setup 開始)
  7. logbot-usb off (即座に、bootup 中に切替) → バッテリー駆動へ
  8. sleep <duration_sec>     (シーケンス完走待ち、default 900s = 15 分)
  9. current_monitor に SIGTERM (or terminate) で clean shutdown
 10. logbot-usb on            (VBUS 復電、次ペアの user 準備に戻す)
 11. meta.json 保存 + screening_utils で features.json 生成

失敗時: finally で必ず current_monitor を kill、logbot-usb on を試みる。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import screening_utils


SCRIPT_DIR = Path(__file__).parent
CURRENT_MEASUREMENT_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / "data"
LOGBOT_REPO = Path(r"C:\Users\tsune\logbot")
LOGBOT_CTL = LOGBOT_REPO / "tools" / "logbot-ctl" / "logbot_ctl.py"
LOGBOT_USB = LOGBOT_REPO / "tools" / "logbot-usb" / "logbot_usb.py"
CURRENT_MONITOR = CURRENT_MEASUREMENT_DIR / "current_monitor.py"
POWER_TEST_SKETCH_DIR = (
    LOGBOT_REPO / "LogbotArduinoCode" / "logbot-v5" / "v5_umineko_wifi_2026"
    / "nrf_power_test_all"
)

# VBUS ON 直後の USB 認識待ち (Windows CP210x + Adafruit Feather CDC が両方認識されるまで)
VBUS_ON_SETTLE_SEC = 5.0
# reset --double 後、Adafruit bootloader が boot するまでの待機
DFU_BOOT_SETTLE_SEC = 3.0
# arduino-cli upload 完了後、V5 が新 firmware で boot して安定するまでの待機
POST_UPLOAD_SETTLE_SEC = 3.0


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """subprocess.run のラッパ。失敗時にログを吐く。"""
    print(f"[run] {' '.join(cmd)}", flush=True)
    kwargs = {}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    try:
        return subprocess.run(cmd, check=check, **kwargs)
    except subprocess.CalledProcessError as e:
        print(f"[run] FAILED rc={e.returncode}", file=sys.stderr, flush=True)
        if capture:
            print(f"  stdout: {e.stdout}", file=sys.stderr, flush=True)
            print(f"  stderr: {e.stderr}", file=sys.stderr, flush=True)
        raise


def logbot_ctl(*args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run([sys.executable, str(LOGBOT_CTL), *args], capture=capture, check=check)


def logbot_usb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run([sys.executable, str(LOGBOT_USB), *args], check=check)


def logbot_usb_cycle_on(cycle_wait_sec: float = 1.0) -> None:
    """VBUS OFF → wait → ON で USB stack を強制 clean state に。

    単純な `logbot-usb on` は既に ON 状態のときに no-op のため、Windows 側の
    USB enumeration が sticky になっていると COM12/COM15 が使用不能のまま。
    cycle で OFF→ON 遷移を発火させる。
    """
    logbot_usb("cycle", "--wait", str(cycle_wait_sec))
    time.sleep(VBUS_ON_SETTLE_SEC)


def start_current_monitor(csv_path: Path, port: str, baud: int) -> subprocess.Popen:
    cmd = [
        sys.executable, str(CURRENT_MONITOR),
        "--headless", "--port", port, "--baud", str(baud),
        "--csv", str(csv_path),
    ]
    print(f"[run] (background) {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)


def upload_power_firmware(if_port: str, v5_port: str) -> dict:
    """電流測定用 firmware を V5 に書き込む (Phase 0)。

    arduino-cli の 1200 baud reset が自動的に bootloader 遷移 → 書き込み → 復帰
    を処理する。1200 baud reset に応答しない firmware への fallback として
    logbot-ctl reset --double も internally 使う (compile_and_upload_v5_with_fallback)。
    """
    print(f"[phase0] arduino-cli で {POWER_TEST_SKETCH_DIR.name} → {v5_port} 書き込み",
          flush=True)
    ok, info = screening_utils.compile_and_upload_v5_with_fallback(
        POWER_TEST_SKETCH_DIR, v5_port, if_port=if_port)
    if not ok:
        raise RuntimeError(
            f"電流測定用 firmware 書き込み失敗: marker={info.get('failure_marker')}, "
            f"stderr_tail={info.get('stderr_tail')}"
        )
    time.sleep(POST_UPLOAD_SETTLE_SEC)
    return info


def stop_current_monitor(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[stop] terminate timeout, killing", file=sys.stderr, flush=True)
        proc.kill()
        proc.wait(timeout=timeout)


def interruptible_sleep(seconds: float, tag: str = "") -> None:
    """1 秒刻みでログを吐きつつ待機 (Ctrl+C で即中断)。"""
    end = time.monotonic() + seconds
    last_log = time.monotonic()
    log_every = 30.0  # 30 秒ごとに進捗
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        if time.monotonic() - last_log >= log_every:
            print(f"[wait {tag}] 残り {remaining:.0f}s / {seconds:.0f}s", flush=True)
            last_log = time.monotonic()
        time.sleep(min(1.0, remaining))


def main() -> int:
    ap = argparse.ArgumentParser(description="1 ペア電流測定オーケストレータ")
    ap.add_argument("--pair-id", type=int, required=True, help="ペア番号 (1..12)")
    ap.add_argument("--if-port", required=True, help="IF ボード側 COM ポート (例: COM10)")
    ap.add_argument("--v5-port", required=True,
                    help="V5 native USB CDC の COM (Phase 0 書き込み + verify で使用)")
    ap.add_argument("--monitor-port", default="COM12", help="INA219 リグの COM (default: COM12)")
    ap.add_argument("--monitor-baud", type=int, default=9600)
    ap.add_argument("--duration-sec", type=float, default=150.0,
                    help="シーケンス完走待ち秒数 (default: 150 = 2.5 分。"
                         "短縮版 nrf_power_test_all は実測 2-2.5 分で完走)")
    ap.add_argument("--baseline-sec", type=float, default=3.0,
                    help="reset 前の baseline 記録秒数 (default: 3)")
    ap.add_argument("--skip-tm", action="store_true", help="RTC 同期をスキップ")
    ap.add_argument("--skip-power-upload", action="store_true",
                    help="Phase 0 (電流測定用 firmware 書き込み) をスキップ")
    ap.add_argument("--no-verify", action="store_true",
                    help="電流測定後の WiFi 動作検証 (Phase B) をスキップ")
    ap.add_argument("--verify-duration-sec", type=float, default=90.0,
                    help="verify の serial 監視秒数 (default: 90)")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"screening_pair{args.pair_id:02d}_{ts}"
    csv_path = DATA_DIR / f"{base}.csv"
    meta_path = DATA_DIR / f"{base}_meta.json"
    features_path = DATA_DIR / f"{base}_features.json"
    verify_path = DATA_DIR / f"{base}_verify.json"

    meta: dict = {
        "pair_id": args.pair_id,
        "if_port": args.if_port,
        "v5_port": args.v5_port,
        "monitor_port": args.monitor_port,
        "monitor_baud": args.monitor_baud,
        "duration_sec": args.duration_sec,
        "csv_path": str(csv_path),
        "verify_path": str(verify_path) if (args.v5_port and not args.no_verify) else None,
        "start_ts": datetime.now().isoformat(),
    }

    monitor_proc: subprocess.Popen | None = None
    try:
        # 0-a. VBUS cycle → USB enumeration 強制再トリガ (単純 on では sticky で効かない)
        logbot_usb_cycle_on()

        # 0-b. Phase 0: 電流測定用 firmware を V5 に書き込み
        if not args.skip_power_upload:
            meta["power_upload"] = upload_power_firmware(args.if_port, args.v5_port)

        # 2-3. info + tm
        info_res = logbot_ctl("info", args.if_port, capture=True)
        meta["logbot_ctl_info_stdout"] = info_res.stdout.strip()
        if not args.skip_tm:
            logbot_ctl("tm", args.if_port)

        # 4. current_monitor 起動 (baseline 記録開始)
        monitor_proc = start_current_monitor(csv_path, args.monitor_port, args.monitor_baud)
        interruptible_sleep(args.baseline_sec, tag="baseline")

        # 5. reset (通常 single reset) → DUT が nrf_power_test_all setup() → シーケンス開始
        # NOTE: --double は DFU モード遷移用。app 起動には通常 reset を使う
        logbot_ctl("reset", args.if_port)

        # 6. 即座に VBUS OFF (bootup 中に切り替え)
        logbot_usb("off")
        meta["vbus_off_ts"] = datetime.now().isoformat()

        # 7. シーケンス完走待ち
        interruptible_sleep(args.duration_sec, tag="sequence")

    except KeyboardInterrupt:
        print("[main] Ctrl+C 受信、cleanup へ", flush=True)
        meta["aborted"] = "KeyboardInterrupt"
    except Exception as e:
        print(f"[main] EXCEPTION: {e}", file=sys.stderr, flush=True)
        meta["aborted"] = repr(e)
    finally:
        # current_monitor 停止
        if monitor_proc is not None:
            stop_current_monitor(monitor_proc)

        # VBUS 復電 (次サイクルのため / エラー時も安全側)
        try:
            logbot_usb("on")
        except Exception as e:
            print(f"[cleanup] logbot-usb on failed: {e}", file=sys.stderr, flush=True)

        meta["end_ts"] = datetime.now().isoformat()

    # WiFi 動作検証 (電流測定が完了した後、VBUS ON 状態で実行)
    verify_summary: dict | None = None
    if not args.no_verify:
        # Phase A 直後は VBUS OFF → cleanup で on 済みだが sticky を避けるため cycle 再実行
        try:
            logbot_usb_cycle_on()
        except Exception as e:
            print(f"[verify] pre-cycle failed: {e}", file=sys.stderr, flush=True)
        try:
            verify_cmd = [
                sys.executable, str(SCRIPT_DIR / "screening_verify.py"),
                "--pair-id", str(args.pair_id),
                "--v5-port", args.v5_port,
                "--if-port", args.if_port,
                "--duration-sec", str(args.verify_duration_sec),
                "--out-prefix", base,
            ]
            print(f"[verify] starting: {' '.join(verify_cmd)}", flush=True)
            r = subprocess.run(verify_cmd, capture_output=True, text=True)
            print(r.stdout, flush=True)
            if r.stderr:
                print(r.stderr, file=sys.stderr, flush=True)
            # verify_verify.py の stdout 最終行が JSON summary
            try:
                last_json_line = [ln for ln in r.stdout.strip().splitlines()
                                  if ln.startswith("{")][-1]
                verify_summary = json.loads(last_json_line)
                meta["verify"] = {
                    "pass": verify_summary.get("pass"),
                    "ok_count": verify_summary.get("ok_count"),
                    "returncode": r.returncode,
                }
            except (IndexError, json.JSONDecodeError):
                meta["verify"] = {
                    "pass": None,
                    "returncode": r.returncode,
                    "error": "JSON parse failed",
                }
        except Exception as e:
            print(f"[verify] EXCEPTION: {e}", file=sys.stderr, flush=True)
            meta["verify"] = {"pass": None, "error": repr(e)}
    else:
        meta["verify"] = {"skipped": True, "reason": "--no-verify"}

    # meta.json 保存
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[done] meta saved: {meta_path}", flush=True)

    # features.json (CSV が存在する場合のみ)
    if csv_path.exists() and csv_path.stat().st_size > 0:
        try:
            df = screening_utils.load_csv(csv_path)
            feats = screening_utils.compute_features(df, args.pair_id, str(csv_path))
            with open(features_path, "w", encoding="utf-8") as f:
                json.dump(feats.to_dict(), f, ensure_ascii=False, indent=2)
            print(f"[done] features saved: {features_path}", flush=True)

            is_outlier, reasons = screening_utils.outlier_verdict(feats)
            summary = {
                "pair_id": args.pair_id,
                "csv": str(csv_path),
                "baseline_median_mA": feats.baseline_median_mA,
                "wifi_on_mean_mA": feats.wifi_on_mean_mA,
                "global_max_mA": feats.global_max_mA,
                "dropout_ratio": feats.dropout_ratio,
                "outlier": is_outlier,
                "reasons": reasons,
            }
            if verify_summary is not None:
                summary["verify_pass"] = verify_summary.get("pass")
                summary["verify_ok_count"] = verify_summary.get("ok_count")
                summary["verify_last_error"] = verify_summary.get("last_error")
            elif not args.no_verify:
                summary["verify_pass"] = None
                summary["verify_error"] = meta.get("verify", {}).get("error", "unknown")
            else:
                summary["verify_skipped"] = True
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        except Exception as e:
            print(f"[done] features 抽出に失敗: {e}", file=sys.stderr, flush=True)
    else:
        print(f"[done] CSV 未生成 or 空 ({csv_path})", file=sys.stderr, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
