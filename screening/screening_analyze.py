"""1 ペア分の CSV → interactive HTML レポート生成。

出力: `screening/reports/pair<N>_YYYYMMDD-HHMMSS.html`
中身: 特徴量サマリ表 + Plotly 電流波形 (zoom/pan 可、self-contained)
stdout: JSON サマリ (Claude が対話で読む用)

呼び方:
  # meta.json 経由 (screening_run.py 直後、pair の最新測定を自動解析)
  python screening_analyze.py --pair-id 1

  # CSV を明示指定
  python screening_analyze.py --csv data/screening_pair01_20260710-160000.csv --pair-id 1
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go

import screening_utils


SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
REPORT_DIR = SCRIPT_DIR / "reports"


def find_latest_csv(pair_id: int) -> Path | None:
    matches = sorted(DATA_DIR.glob(f"screening_pair{pair_id:02d}_*.csv"))
    return matches[-1] if matches else None


def find_meta_for_csv(csv_path: Path) -> Path | None:
    meta = csv_path.with_name(csv_path.stem + "_meta.json")
    return meta if meta.exists() else None


def find_verify_for_csv(csv_path: Path) -> Path | None:
    verify = csv_path.with_name(csv_path.stem + "_verify.json")
    return verify if verify.exists() else None


def render_verify_section(verify_data: dict | None) -> str:
    if not verify_data:
        return ('<div class="verdict skipped">verify データなし '
                '(screening_run.py --v5-port 未指定 or --no-verify で skip されました)</div>')

    summary = verify_data.get("summary", verify_data)
    passed = summary.get("pass")
    ok_count = summary.get("ok_count", 0)
    min_ok = summary.get("min_testok", 3)
    duration = summary.get("duration_sec", 0)
    last_error = summary.get("last_error", "")
    v5_port = summary.get("v5_port", "?")

    if passed is True:
        cls, msg = "ok", f"✓ PASS — 「-> テストOK」を {ok_count} 回検出 (閾値 {min_ok})"
    elif passed is False:
        cls, msg = "outlier", f"✗ FAIL — 「-> テストOK」検出 {ok_count} 回 (閾値 {min_ok})"
        if last_error:
            msg += f" / 最後の error: {html.escape(last_error)}"
    else:
        cls, msg = "skipped", f"? UNKNOWN — verify 結果を解釈できず"

    verdict_html = f'<div class="verdict {cls}"><strong>{msg}</strong></div>'

    meta_rows = [
        f"<tr><th>V5 port</th><td>{html.escape(str(v5_port))}</td></tr>",
        f"<tr><th>Duration</th><td>{duration} s</td></tr>",
        f"<tr><th>OK count</th><td>{ok_count} / (min {min_ok})</td></tr>",
        f"<tr><th>Log lines</th><td>{summary.get('log_line_count', '?')}</td></tr>",
    ]
    meta_table = f'<table class="summary">{"".join(meta_rows)}</table>'

    log_lines = verify_data.get("log_full") or summary.get("log_tail") or []
    if log_lines and isinstance(log_lines[0], dict):
        log_items = [f"[{ln['ts']}] {ln['line']}" for ln in log_lines[-40:]]
    else:
        log_items = [str(ln) for ln in log_lines[-40:]]
    log_html = ("<pre class=\"log\">" + html.escape("\n".join(log_items)) + "</pre>"
                if log_items else "<p>(log なし)</p>")

    return verdict_html + meta_table + "<h3>シリアルログ (末尾 40 行)</h3>" + log_html


def render_summary_table(feats: screening_utils.Features, is_outlier: bool, reasons: list[str]) -> str:
    def row(label: str, value: str, warn: bool = False) -> str:
        cls = ' class="warn"' if warn else ""
        return f"<tr{cls}><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"

    fmt = lambda v, unit="": f"{v:.3f} {unit}".strip() if v is not None else "N/A"

    def phase_row(name: str, count: int, mean_mA, max_mA, dur_s: float) -> str:
        return (
            f'<tr><th>{html.escape(name)}</th>'
            f'<td>burst {count}</td>'
            f'<td>mean {fmt(mean_mA, "mA")}</td>'
            f'<td>max {fmt(max_mA, "mA")}</td>'
            f'<td>total {fmt(dur_s, "s")}</td></tr>'
        )

    rows = [
        row("Pair ID", str(feats.pair_id)),
        row("CSV", feats.csv_path),
        row("Sample count", f"{feats.sample_count:,}"),
        row("Duration", fmt(feats.duration_s, "s")),
        row("Dropout ratio", f"{feats.dropout_ratio:.2%}",
            warn=feats.dropout_ratio > 0.05),
        row("Baseline median (20-25 s)", fmt(feats.baseline_median_mA, "mA"),
            warn=feats.baseline_median_mA > 1.0),
        row("Baseline p95 (20-25 s)", fmt(feats.baseline_p95_mA, "mA"),
            warn=feats.baseline_p95_mA > 20.0),
        row("Global mean", fmt(feats.global_mean_mA, "mA")),
        row("Global max", fmt(feats.global_max_mA, "mA")),
    ]

    phase_rows = [
        phase_row("WiFi (burst 3-10s, >100 mA)",
                  feats.wifi_burst_count, feats.wifi_mean_mA,
                  feats.wifi_max_mA, feats.wifi_total_dur_s),
        phase_row("Camera (burst 15-30s, >100 mA)",
                  feats.camera_burst_count, feats.camera_mean_mA,
                  feats.camera_max_mA, feats.camera_total_dur_s),
        phase_row("SD write (burst <2s, 30-100 mA)",
                  feats.sd_burst_count, feats.sd_mean_mA,
                  feats.sd_max_mA, feats.sd_total_dur_s),
    ]

    verdict_html = ""
    if is_outlier:
        reasons_html = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
        verdict_html = f'<div class="verdict outlier"><strong>⚠ OUTLIER</strong><ul>{reasons_html}</ul></div>'
    else:
        verdict_html = '<div class="verdict ok"><strong>✓ 絶対閾値内</strong> (相対 outlier 判定は overview で)</div>'

    return (
        f'{verdict_html}'
        f'<table class="summary">{"".join(rows)}</table>'
        f'<h3 style="margin-top:16px">Phase 別 (burst 自動分類)</h3>'
        f'<table class="summary">{"".join(phase_rows)}</table>'
    )


def build_figure(df) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["t_sec"],
        y=df["I_mA"],
        mode="lines",
        name="Current (mA)",
        line=dict(width=1, color="#1f77b4"),
        hovertemplate="t=%{x:.2f} s<br>I=%{y:.2f} mA<extra></extra>",
    ))

    # 閾値ライン
    fig.add_hline(y=screening_utils.WIFI_ON_THRESHOLD_MA, line_dash="dot",
                  line_color="#888", annotation_text="WiFi ON threshold (30 mA)",
                  annotation_position="top left")
    fig.add_hline(y=screening_utils.HIGH_CURRENT_THRESHOLD_MA, line_dash="dot",
                  line_color="#f88", annotation_text="High current threshold (100 mA)",
                  annotation_position="top left")
    fig.add_hline(y=1.0, line_dash="dot",
                  line_color="#f00", annotation_text="Baseline warning (1 mA)",
                  annotation_position="bottom left")

    # baseline window (5-25 s) を薄く塗り
    fig.add_vrect(x0=screening_utils.BASELINE_START_S, x1=screening_utils.BASELINE_END_S,
                  fillcolor="#dfd", opacity=0.3, line_width=0,
                  annotation_text="baseline window", annotation_position="top left")

    fig.update_layout(
        title="Current (mA) vs elapsed time (s)",
        xaxis_title="Elapsed time (s)",
        yaxis_title="Current (mA)",
        height=520,
        margin=dict(l=60, r=30, t=60, b=60),
        hovermode="x unified",
    )
    fig.update_xaxes(rangeslider_visible=True)
    return fig


HTML_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Pair {pair_id:02d} Screening Report ({ts})</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 24px; color: #222; }}
  h1 {{ margin: 0 0 8px; font-size: 1.6em; }}
  h2 {{ margin: 24px 0 8px; font-size: 1.2em; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 0.95em; }}
  table.summary {{ border-collapse: collapse; margin: 8px 0 16px; }}
  table.summary th, table.summary td {{ padding: 4px 12px; border: 1px solid #ddd; text-align: left; }}
  table.summary th {{ background: #f5f5f5; font-weight: 600; }}
  table.summary tr.warn td {{ background: #fee; color: #900; font-weight: bold; }}
  .verdict {{ padding: 10px 14px; margin: 12px 0; border-radius: 6px; }}
  .verdict.ok {{ background: #eef8ee; border: 1px solid #6c6; }}
  .verdict.outlier {{ background: #fee; border: 1px solid #c66; }}
  .verdict.skipped {{ background: #f5f5f5; border: 1px solid #bbb; color: #666; }}
  .verdict ul {{ margin: 6px 0 0; padding-left: 22px; }}
  pre.log {{ background: #1e1e1e; color: #ddd; padding: 12px; border-radius: 4px; max-height: 400px; overflow: auto; font-size: 0.85em; line-height: 1.35; }}
</style>
</head>
<body>
<h1>Pair {pair_id:02d} — Current Screening Report</h1>
<div class="subtitle">Generated: {ts} — CSV: <code>{csv_path}</code></div>

<h2>WiFi 動作検証 (加工前チェック)</h2>
{verify_section}

<h2>電流測定 — 特徴量サマリ</h2>
{summary_table}

<h2>電流波形</h2>
{plot_html}

<h2>メタデータ</h2>
<pre>{meta_json}</pre>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="1 ペア分の CSV → interactive HTML レポート")
    ap.add_argument("--pair-id", type=int, required=True, help="ペア番号 (1..12)")
    ap.add_argument("--csv", type=Path, help="CSV パス (省略時: pair-id の最新)")
    args = ap.parse_args()

    csv_path = args.csv or find_latest_csv(args.pair_id)
    if csv_path is None or not csv_path.exists():
        print(f"error: pair{args.pair_id:02d} の CSV が見つかりません", file=sys.stderr)
        return 2

    df = screening_utils.load_csv(csv_path)
    feats = screening_utils.compute_features(df, args.pair_id, str(csv_path))
    is_outlier, reasons = screening_utils.outlier_verdict(feats)

    meta = {}
    meta_path = find_meta_for_csv(csv_path)
    if meta_path:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

    verify_data = None
    verify_path = find_verify_for_csv(csv_path)
    if verify_path:
        with open(verify_path, "r", encoding="utf-8") as f:
            verify_data = json.load(f)

    fig = build_figure(df)
    plot_html = fig.to_html(include_plotlyjs="inline", full_html=False,
                            config={"displaylogo": False})

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = HTML_TEMPLATE.format(
        pair_id=args.pair_id,
        ts=ts_str,
        csv_path=html.escape(str(csv_path)),
        verify_section=render_verify_section(verify_data),
        summary_table=render_summary_table(feats, is_outlier, reasons),
        plot_html=plot_html,
        meta_json=html.escape(json.dumps(meta, ensure_ascii=False, indent=2)),
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"pair{args.pair_id:02d}_{csv_path.stem.split('_', 2)[-1]}.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_body)

    print(f"[report] {report_path}", flush=True)
    summary_out = {
        "pair_id": args.pair_id,
        "csv": str(csv_path),
        "report": str(report_path),
        "baseline_median_mA": feats.baseline_median_mA,
        "wifi_on_mean_mA": feats.wifi_on_mean_mA,
        "global_max_mA": feats.global_max_mA,
        "dropout_ratio": feats.dropout_ratio,
        "outlier": is_outlier,
        "reasons": reasons,
    }
    if verify_data:
        v = verify_data.get("summary", verify_data)
        summary_out["verify_pass"] = v.get("pass")
        summary_out["verify_ok_count"] = v.get("ok_count")
    print(json.dumps(summary_out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
