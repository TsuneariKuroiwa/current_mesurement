"""WiFi 動作検証 (加工前チェック)。

nrf_wifi_connection_test.ino を V5 に書き込み、シリアル 115200 baud を pyserial で
キャプチャ、"-> テストOK" を N 回検出できたら PASS。

nrf_wifi_connection_test.ino:
  - 起動後、10 秒 WiFi スキャンを 5 秒間隔で繰り返す
  - Serial 115200 に "-> テストOK" / "-> テストNG" / "エラー: ..." を出す
  - ESP32 が応答しない場合 "エラー: ESP32から応答がありません" + "-> テストNG (通信失敗)"

呼び方:
  # 単体 (電流測定後、V5 port を指定して)
  python screening_verify.py --pair-id 1 --v5-port COM15 --if-port COM12

  # screening_run.py から自動呼び出し (--if-port も自動渡し)
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
DATA_DIR = SCRIPT_DIR / "data"
SKETCH_DIR = Path(
    r"C:\Users\tsune\logbot\LogbotArduinoCode\logbot-v5\v5_umineko_wifi_2026\nrf_wifi_connection_test"
)

PATTERN_OK = ("テストOK",)             # "-> テストOK" / "-> テストOK (スキャン完了...)"
PATTERN_NG = ("テストNG", "エラー:")

POST_UPLOAD_SETTLE_SEC = 8.0  # 書き込み後 V5 が boot → setup() → Serial 準備完了まで
SERIAL_OPEN_RETRY = 5
SERIAL_OPEN_RETRY_INTERVAL_SEC = 2.0


def upload_test_firmware(v5_port: str, if_port: str | None = None) -> dict:
    """nrf_wifi_connection_test.ino を V5 に書き込み。

    arduino-cli の 1200 baud reset で bootloader 遷移が自動で行われる。
    if_port 指定時、失敗した場合は logbot-ctl reset --double で hardware DFU fallback。
    """
    print(f"[verify] arduino-cli で {SKETCH_DIR.name} → {v5_port} 書き込み", flush=True)
    ok, info = screening_utils.compile_and_upload_v5_with_fallback(
        SKETCH_DIR, v5_port, if_port=if_port)
    if not ok:
        raise RuntimeError(
            f"検証用 firmware 書き込み失敗: marker={info.get('failure_marker')}, "
            f"stderr_tail={info.get('stderr_tail')}"
        )
    time.sleep(POST_UPLOAD_SETTLE_SEC)
    return info


def open_serial_with_retry(port: str, baud: int = 115200):
    """arduino-cli upload 直後は COM がまだ握られている可能性があるので retry。"""
    import serial
    last_err = None
    for attempt in range(SERIAL_OPEN_RETRY):
        try:
            return serial.Serial(port, baud, timeout=1)
        except serial.SerialException as e:
            last_err = e
            print(f"[verify] serial open attempt {attempt + 1}/{SERIAL_OPEN_RETRY} 失敗: {e}",
                  flush=True)
            time.sleep(SERIAL_OPEN_RETRY_INTERVAL_SEC)
    raise last_err  # type: ignore[misc]


def capture_serial(v5_port: str, duration_sec: float, min_ok: int
                   ) -> tuple[int, list[tuple[str, str]], str]:
    """serial を duration_sec 秒 (or min_ok 個 OK 検出) 監視。"""
    import serial

    log: list[tuple[str, str]] = []
    ok_count = 0
    last_error = ""

    try:
        sp = open_serial_with_retry(v5_port, 115200)
    except serial.SerialException as e:
        return 0, log, f"serial open failed after {SERIAL_OPEN_RETRY} retries: {e}"

    try:
        end = time.monotonic() + duration_sec
        last_progress = time.monotonic()
        while time.monotonic() < end:
            try:
                raw = sp.readline()
            except serial.SerialException as e:
                last_error = f"serial read error: {e}"
                break
            if not raw:
                if time.monotonic() - last_progress > 15:
                    print(f"[verify] 15s 無出力、残り {end - time.monotonic():.0f}s", flush=True)
                    last_progress = time.monotonic()
                continue
            try:
                line = raw.decode(errors="replace").rstrip()
            except Exception:
                continue
            if not line:
                continue
            ts = datetime.now().isoformat(timespec="milliseconds")
            log.append((ts, line))
            print(f"[serial] {line}", flush=True)
            last_progress = time.monotonic()
            if any(p in line for p in PATTERN_OK):
                ok_count += 1
                if ok_count >= min_ok:
                    break
            elif any(p in line for p in PATTERN_NG):
                last_error = line
    finally:
        sp.close()

    return ok_count, log, last_error


def main() -> int:
    ap = argparse.ArgumentParser(description="WiFi 動作検証 (加工前チェック)")
    ap.add_argument("--pair-id", type=int, required=True, help="ペア番号 (1..12)")
    ap.add_argument("--v5-port", required=True,
                    help="V5 native USB CDC の COM ポート (例: COM15)")
    ap.add_argument("--if-port",
                    help="IF ボード COM (arduino-cli 書き込み失敗時の logbot-ctl reset --double fallback 用)")
    ap.add_argument("--duration-sec", type=float, default=90.0,
                    help="serial 監視秒数 (default: 90)")
    ap.add_argument("--min-testok", type=int, default=3,
                    help="PASS 判定に必要な テストOK 検出回数 (default: 3)")
    ap.add_argument("--skip-upload", action="store_true",
                    help="firmware 書き込みをスキップ (書き込み済みの場合)")
    ap.add_argument("--out-prefix", default=None,
                    help="出力 ファイル名 prefix (default: screening_pairNN_YYYYMMDD-HHMMSS)")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.out_prefix is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.out_prefix = f"screening_pair{args.pair_id:02d}_{ts}"
    verify_path = DATA_DIR / f"{args.out_prefix}_verify.json"

    upload_result: dict = {}
    if not args.skip_upload:
        try:
            upload_result = upload_test_firmware(args.v5_port, if_port=args.if_port)
        except (subprocess.CalledProcessError, RuntimeError) as e:
            result = {
                "pair_id": args.pair_id,
                "v5_port": args.v5_port,
                "upload_error": str(e),
                "pass": False,
                "reason": "firmware upload failed",
            }
            with open(verify_path, "w", encoding="utf-8") as f:
                json.dump({"summary": result, "log_full": []}, f, ensure_ascii=False, indent=2)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 3

    ok_count, log, last_error = capture_serial(
        args.v5_port, args.duration_sec, args.min_testok
    )
    passed = ok_count >= args.min_testok

    result = {
        "pair_id": args.pair_id,
        "v5_port": args.v5_port,
        "upload": upload_result,
        "duration_sec": args.duration_sec,
        "min_testok": args.min_testok,
        "ok_count": ok_count,
        "last_error": last_error,
        "log_line_count": len(log),
        "pass": passed,
    }

    # verify.json (フル ログ含む) 保存
    with open(verify_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": result,
            "log_full": [{"ts": ts, "line": ln} for ts, ln in log],
        }, f, ensure_ascii=False, indent=2)

    # stdout に summary
    result["verify_path"] = str(verify_path)
    result["log_tail"] = [{"ts": ts, "line": ln} for ts, ln in log[-15:]]
    print(json.dumps(result, ensure_ascii=False), flush=True)

    return 0 if passed else 4


if __name__ == "__main__":
    sys.exit(main())
