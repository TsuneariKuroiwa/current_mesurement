# Screening — 加工前 電流測定 + WiFi 動作検証 (12 ペア)

**2026 年 8 月オオミズナギドリ WiFi ロガー実験** の加工前チェック。**WiFi 基板 N 号 + V5 本体 N 号** の 12 ペアに対して以下を 1 サイクルで実施:

1. **電流測定** — `nrf_power_test_all.ino` (~15 分) で `device1` 型 (**+20-30 mA 余分な常時消費**) の異常個体を検出
2. **WiFi 動作検証** — `nrf_wifi_connection_test.ino` (~90 秒) で加工後変更不可な WiFi 通信路が正常か確認 (`-> テストOK` 出力を N 回検出で PASS)

対話ラリー方式: **user がハード準備 → Claude Code が本スクリプトを呼び出し → Claude が Plotly HTML で報告 → user が次ペアを準備**。

## 構成

```
current_measurement/
├── current_monitor.py           # ヘッドレス対応済み (INA219 → CSV)
├── current_measurement.ino      # INA219 側 Arduino スケッチ (既存)
└── screening/
    ├── screening_run.py         # 1 ペア測定オーケストレータ
    ├── screening_analyze.py     # 1 ペア HTML レポート
    ├── screening_overview.py    # 12 ペア集約 HTML
    ├── screening_utils.py       # 共通ユーティリティ
    ├── README.md                # このファイル
    ├── data/                    # CSV + meta.json + features.json
    └── reports/                 # Plotly HTML (pair<N>_*.html, overview_*.html)
```

## 事前準備 (1 回だけ)

### ハードウェア
- **INA219 リグ**: Arduino + INA219 モジュール、`current_measurement.ino` を書き込み済み、シリアルポート COM12 (default)。バッテリー ⇔ V5 の間に直列接続。
- **X-RL1 USB リレー**: `~/logbot/tools/logbot-usb/README.md` の配線通り、A→C ケーブルの VBUS だけリレーで断続可能に改造済み。V5 の USB (Adafruit Feather nRF52840 の native USB) 側に噛ませる。
- **IF ボード**: V5 IF ボード側 CP210x USB は PC の別ポートに直接接続 (X-RL1 を経由しない)。

### ソフトウェア

```powershell
# venv 有効化
C:\Users\tsune\current_measurement\venv\Scripts\Activate.ps1

# 依存確認 (plotly が新規追加)
pip install -r C:\Users\tsune\current_measurement\requirements.txt
```

### ファームウェア

**事前 bulk 書き込みは不要** — `screening_run.py` がペアごとに Phase 0 で自動書き込みする。

書き込まれる firmware:

| firmware | 書き込み先 | タイミング | 加工後変更 |
|---|---|---|---|
| `nrf_power_test_all.ino` | V5 (nRF52840) | 毎ペア Phase 0 (自動) | 可 |
| `nrf_wifi_connection_test.ino` | V5 (nRF52840) | 毎ペア Phase B (自動) | 可 |
| `esp32_command.ino` | WiFi 基板 (ESP32) | **事前必須 (user)** | **不可** |

ESP32 側 `esp32_command.ino` の書き込み手順は `~/logbot/LogbotArduinoCode/logbot-v5/v5_umineko_wifi_2026/README.md` 参照 (SH-U06A + 手動方式)。加工後変更不可なので、加工前に必ず本番用を焼いておく。

### 個体 ID 固定

**マスキングテープに `pair 01`〜`pair 12` を書いて WiFi 基板 + V5 本体 の両方に貼る** (混同防止)。

### COM 対応

`~/logbot/.claude/local_env.md` の `V5 ロガー COM 対応` テーブルを 12 台分に拡張して IF port を記録。USB シリアル番号ベースで固定されるので、一度書けば以降差し替え不要。

### バッテリー電圧

3 時間の連続測定になるので、開始前に全 LiPo バッテリー電圧を実測 (**4.05 V 以上**を推奨、`~/rocky/skills/logbot-*` 参照)。

## 対話ラリー (1 サイクル ~15-20 分)

### user 手順 (~3-5 分)

1. pair N の WiFi 基板 + V5 本体をコネクタで結合、マスキングテープに `pair NN` 記入
2. LiPo バッテリー接続 (4.05 V 以上を実測確認)
3. V5 の USB を **改造 A→C ケーブル (X-RL1 経由)** に接続
4. V5 の IF ボード USB を PC の別ポートに接続
5. Claude に「**pair N 準備 OK、IF=COMxx、V5=COMyy**」と伝える
   - `IF` = IF ボード CP210x の COM (`logbot-ctl reset` 用)
   - `V5` = V5 native USB CDC の COM (WiFi 動作検証 firmware の書き込み + シリアル監視用)
   - **前回と同じペアなら USB シリアル番号ベースで COM は変わらない**。`local_env.md` に記録されていれば毎回言わなくて可

### Claude 手順 (~18-19 分)

Claude が対話内で以下を 1 コマンドで invoke:

```powershell
python C:\Users\tsune\current_measurement\screening\screening_run.py `
  --pair-id N --if-port COMxx --v5-port COMyy
```

#### Phase 0: 電流測定用 firmware 書き込み (~30-60 秒)

1. `logbot-usb on` (VBUS ON 明示)
2. 5 秒待機 (Windows USB 認識、CP210x + Feather CDC 両方)
3. `logbot-ctl reset <IF_PORT> --double` (nRF52 hardware reset → DFU モード強制、1200 baud reset が効かない firmware 対策)
4. 3 秒待機 (bootloader boot)
5. `arduino-cli compile -u -p <V5_PORT>` で **`nrf_power_test_all.ino`** を V5 に書き込み
6. 3 秒待機 (書き込み後 boot)
7. `--skip-power-upload` で skip 可 (既に書き込み済みの場合)

#### Phase A: 電流測定 (~15 分)

**大原則: `logbot-usb off` の後は V5 と通信不能。`logbot-ctl` は VBUS ON 中のみ、`logbot-usb off` は必ず `reset` の直後**

8. `logbot-ctl info <IF_PORT>` (F/W 版と Board を記録)
9. `logbot-ctl tm <IF_PORT>` (RTC 同期)
10. `current_monitor.py --headless` (別プロセスで CSV 記録開始、`--port COMxx` で INA219 リグの COM 指定)
11. 3 秒 baseline 記録
12. `logbot-ctl reset <IF_PORT> --double` (DUT リセット、nrf_power_test_all の setup 開始)
13. **即座に `logbot-usb off`** (bootup 中に VBUS OFF、バッテリー駆動へ)
14. 15 分待機 (nrf_power_test_all の phase 1-4 完走)
15. `current_monitor` を terminate → CSV flush + close
16. `logbot-usb on` (VBUS 復電、Phase B のため)

#### Phase B: WiFi 動作検証 (~90-120 秒) — `--no-verify` で skip 可

17. 5 秒待機 (VBUS ON 後の USB 認識)
18. `logbot-ctl reset <IF_PORT> --double` (nRF52 を DFU モードに強制)
19. 3 秒待機
20. `arduino-cli compile -u -p <V5_PORT>` で **`nrf_wifi_connection_test.ino`** を書き込み
21. pyserial で `<V5_PORT> @ 115200` を最大 90 秒監視 (open は 5 回 retry で PermissionError 対策)
22. `-> テストOK` を **3 回検出** で PASS 判定 (5 秒間隔で自動再テストされる)
23. `-> テストNG` / `エラー: ESP32から応答がありません` は last_error として記録
24. verify.json 保存 (summary + フル シリアルログ)

#### レポート生成

```powershell
python C:\Users\tsune\current_measurement\screening\screening_analyze.py --pair-id N
```

- `reports/pair<NN>_YYYYMMDD-HHMMSS.html` 生成
  - **WiFi 動作検証 (加工前チェック)** セクション: PASS/FAIL + シリアルログ末尾
  - **電流測定 特徴量サマリ** セクション: baseline / WiFi ON mean / outlier 判定
  - **電流波形** セクション: Plotly interactive
- stdout に JSON サマリ (baseline + verify_pass)
- Claude が対話に「pair<N>: 電流測定 baseline X mA, WiFi ON Y mA, verify PASS/FAIL、詳細: pair<NN>_*.html」を返す

### 全 12 ペア完了後

```powershell
python C:\Users\tsune\current_measurement\screening\screening_overview.py
```

- `reports/overview_YYYYMMDD-HHMMSS.html` 生成 (12 ペア横並び + IQR×1.5 outlier ハイライト)
- outlier があれば加工除外候補として提示

## 単発コマンド例

```powershell
# pair 3 をフル (Phase 0 → 電流測定 → WiFi 検証)
python screening/screening_run.py --pair-id 3 --if-port COM12 --v5-port COM15

# 電流測定のみ (verify skip)
python screening/screening_run.py --pair-id 3 --if-port COM12 --v5-port COM15 --no-verify

# 電流測定 firmware 書き込みを skip (既に書き込み済みの場合)
python screening/screening_run.py --pair-id 3 --if-port COM12 --v5-port COM15 --skip-power-upload

# 時間短縮 (WiFi のみ ~8 分で打ち切り)
python screening/screening_run.py --pair-id 3 --if-port COM12 --v5-port COM15 --duration-sec 480

# RTC 同期スキップ (連続測定で節約)
python screening/screening_run.py --pair-id 3 --if-port COM12 --v5-port COM15 --skip-tm

# WiFi 動作検証のみ (電流測定は既に済んでいる場合、--if-port で DFU 強制遷移 推奨)
python screening/screening_verify.py --pair-id 3 --v5-port COM15 --if-port COM12

# 過去 CSV を再解析 (verify JSON があれば自動で含める)
python screening/screening_analyze.py --pair-id 3 --csv data/screening_pair03_20260710-160000.csv
```

## 期待値 (ウミネコ実測レンジ)

### 電流測定

| 特徴量 | ウミネコ実測 | 異常判定 (絶対閾値) |
|---|---|---|
| baseline_median | ~0.2 mA | **> 1.0 mA** → device1 型異常の疑い |
| baseline_p95 | ~0.5 mA | > 5.0 mA → baseline に spike |
| WiFi ON mean | 106-167 mA | > 250 mA → 大幅超過 |
| Global max | 275-407 mA | (相対比較のみ) |
| dropout_ratio | ~0% | > 5% → 電流モニタ欠損 |

### WiFi 動作検証

| 出力 | 意味 | 判定 |
|---|---|---|
| `→ テストOK` × 3 回 | 5 秒間隔スキャンで連続 OK | PASS |
| `→ テストOK (スキャン完了、デバイス未検出)` | 通信は正常だが周囲に WiFi デバイスなし | OK (研究室内なら OK ではないが装着環境なら想定内) |
| `エラー: ESP32から応答がありません` + `-> テストNG (通信失敗)` | UART or ESP32 電源 or 配線異常 | FAIL |
| READY 未受信 | ESP32 boot 遅延 or 起動失敗 | 継続監視 (FAIL とは限らない) |

**カメラ動作確認は本 workflow のスコープ外** — 物理的に撮影して目視確認が必要。加工前に別途実施 (`~/logbot/LogbotArduinoCode/logbot-v5/v5_umineko_wifi_2026/README.md#加工前チェックリスト` 参照)。

参照: `~/logbot/LogbotArduinoCode/logbot-v5/v5_umineko_wifi_2026/power_consumption_report.md`

## トラブルシュート

| 症状 | 疑い | 対処 |
|---|---|---|
| VBUS OFF なのに電流が流れない (全体 0 mA) | バッテリー未接続 | LiPo コネクタ確認 |
| current_monitor が読めない (CSV 空) | INA219 リグの電源 or COM12 誤り | Arduino IDE で `current_measurement.ino` の Serial 出力を目視 |
| `logbot-ctl reset` が失敗する | IF port 誤り or VBUS OFF 状態 | `logbot-ctl info <PORT>` で先に生存確認 |
| baseline が 5 mA 前後で高止まり | 過渡 (VBUS OFF 直後のコンデンサ放電) | `--baseline-sec` を長めに / データ後半で baseline 再評価 |
| `logbot-usb` が「X-RL1 が複数」エラー | 別の X-RL1 と競合 | `logbot-usb status` で port 特定 → `--port COMx` 明示 |
| Ctrl+C で止めた後、次ペアで DUT が起動しない | VBUS OFF のまま残った | `python -m logbot_usb on` で明示復電 |
| verify で `arduino-cli compile -u 失敗` | V5 port 誤り or ボード認識失敗 | `arduino-cli board list` で V5 port 確認、`local_env.md` と一致するか |
| verify で `-> テストOK` 検出 0 回 | ESP32 応答なし or 周囲に WiFi デバイスなし | シリアルログの末尾を確認: `テストNG` なら配線/ESP32 疑い、`エラー` なら通信路異常 |
| verify で serial open failed | V5 port が別プロセスに掴まれている | 他のシリアルモニタを閉じる (VS Code Arduino, PuTTY 等) |
| `Timed out waiting for acknowledgement` (firmware upload) | nRF52 が DFU モードに入らなかった | 初期 firmware が 1200 baud reset に応答しない場合発生。screening_run.py は `logbot-ctl reset --double` で対策済み。それでも失敗する場合は VBUS cycle (`logbot-usb cycle`) 後に再実行 |
| `logbot-ctl info` が open failed | VBUS ON 直後の USB 認識未完了 | `VBUS_ON_SETTLE_SEC` を長め (5→8) にする |

## 参照

- 実装プラン: `~/.claude/plans/wifi-wifi-12-v5-16-eager-conway.md`
- 消費電流レポート (ウミネコ): `~/logbot/LogbotArduinoCode/logbot-v5/v5_umineko_wifi_2026/power_consumption_report.md`
- Notion タスク: https://app.notion.com/p/399366a22196812dbb08c63055e04948
