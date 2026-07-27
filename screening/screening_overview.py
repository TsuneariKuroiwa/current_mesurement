"""12 ペア集約 HTML レポート生成。

入力: `screening/data/screening_pair*_features.json` (screening_run.py が生成)
出力: `screening/reports/overview_YYYYMMDD-HHMMSS.html`
内容:
  - 特徴量サマリ表 (全 pair 横並び、IQR×1.5 outlier を赤ハイライト)
  - 特徴量ごとの bar chart (baseline / wifi_on_mean / global_max)
  - 判定サマリ (絶対閾値 outlier + 相対 outlier)

呼び方:
  python screening_overview.py                # data/ 配下の全 features を集約
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go

import screening_utils


SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
REPORT_DIR = SCRIPT_DIR / "reports"


def iqr_outliers(values: list[float]) -> tuple[float, float, list[bool]]:
    """IQR × 1.5 で outlier フラグ list を返す。None は False。"""
    clean = [v for v in values if v is not None]
    if len(clean) < 4:
        return float("nan"), float("nan"), [False] * len(values)
    clean_sorted = sorted(clean)
    q1 = statistics.quantiles(clean_sorted, n=4)[0]
    q3 = statistics.quantiles(clean_sorted, n=4)[2]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    flags = [(v is not None and (v < lo or v > hi)) for v in values]
    return lo, hi, flags


def bar_figure(pairs: list[int], values: list[float | None], outlier_flags: list[bool],
               lo: float, hi: float, title: str, y_label: str) -> go.Figure:
    fig = go.Figure()
    colors = ["#e74c3c" if f else "#3498db" for f in outlier_flags]
    y_display = [v if v is not None else 0 for v in values]
    text_display = [f"{v:.2f}" if v is not None else "N/A" for v in values]
    fig.add_trace(go.Bar(
        x=[f"pair{p:02d}" for p in pairs],
        y=y_display,
        marker_color=colors,
        text=text_display,
        textposition="outside",
        hovertemplate="%{x}<br>%{y:.3f}<extra></extra>",
    ))
    if not (lo != lo or hi != hi):  # NaN チェック
        fig.add_hline(y=lo, line_dash="dot", line_color="#e74c3c",
                      annotation_text=f"IQR lower {lo:.2f}", annotation_position="bottom right")
        fig.add_hline(y=hi, line_dash="dot", line_color="#e74c3c",
                      annotation_text=f"IQR upper {hi:.2f}", annotation_position="top right")
    fig.update_layout(
        title=title,
        yaxis_title=y_label,
        xaxis_title="Pair",
        height=380,
        margin=dict(l=60, r=30, t=60, b=60),
    )
    return fig


def render_summary_table(features_list: list[screening_utils.Features],
                         outlier_map: dict[str, list[bool]],
                         abs_outliers: dict[int, list[str]]) -> str:
    headers = [
        "Pair", "baseline_median_mA", "baseline_p95_mA",
        "global_mean_mA", "global_max_mA",
        "wifi_on_mean_mA", "high_current_mean_mA",
        "dropout_ratio", "duration_s",
    ]

    def cell(value, warn: bool) -> str:
        cls = ' class="warn"' if warn else ""
        if value is None:
            text = "N/A"
        elif isinstance(value, float):
            text = f"{value:.3f}"
        else:
            text = str(value)
        return f"<td{cls}>{html.escape(text)}</td>"

    def row(i: int, f: screening_utils.Features) -> str:
        pair_link = f'<a href="./pair{f.pair_id:02d}_*.html">pair{f.pair_id:02d}</a>'
        abs_out = f.pair_id in abs_outliers
        cells = [f'<td>{pair_link}{"⚠" if abs_out else ""}</td>']
        cells.append(cell(f.baseline_median_mA, outlier_map["baseline_median_mA"][i]))
        cells.append(cell(f.baseline_p95_mA, outlier_map["baseline_p95_mA"][i]))
        cells.append(cell(f.global_mean_mA, outlier_map["global_mean_mA"][i]))
        cells.append(cell(f.global_max_mA, outlier_map["global_max_mA"][i]))
        cells.append(cell(f.wifi_on_mean_mA, outlier_map["wifi_on_mean_mA"][i]))
        cells.append(cell(f.high_current_mean_mA, outlier_map["high_current_mean_mA"][i]))
        cells.append(cell(f.dropout_ratio, f.dropout_ratio > 0.05))
        cells.append(cell(f.duration_s, False))
        return f"<tr>{''.join(cells)}</tr>"

    header_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    rows_html = "".join(row(i, f) for i, f in enumerate(features_list))
    return f'<table class="summary"><thead><tr>{header_html}</tr></thead><tbody>{rows_html}</tbody></table>'


HTML_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Screening Overview ({ts})</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 24px; color: #222; }}
  h1 {{ margin: 0 0 8px; font-size: 1.6em; }}
  h2 {{ margin: 24px 0 8px; font-size: 1.2em; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 0.95em; }}
  table.summary {{ border-collapse: collapse; margin: 8px 0 16px; font-size: 0.92em; }}
  table.summary th, table.summary td {{ padding: 4px 10px; border: 1px solid #ddd; text-align: right; }}
  table.summary th {{ background: #f5f5f5; font-weight: 600; text-align: center; }}
  table.summary td.warn {{ background: #fee; color: #900; font-weight: bold; }}
  .verdicts {{ padding: 12px 16px; border-radius: 6px; margin: 12px 0; }}
  .verdicts.warn {{ background: #fee; border: 1px solid #c66; }}
  .verdicts.ok {{ background: #eef8ee; border: 1px solid #6c6; }}
  .verdicts ul {{ margin: 6px 0; padding-left: 22px; }}
  .plot-grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
  code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Screening Overview — 電流測定サマリ</h1>
<div class="subtitle">Generated: {ts} — Pairs: {pair_count}</div>

<h2>判定サマリ</h2>
{verdict_html}

<h2>特徴量テーブル</h2>
<p>⚠ = 絶対閾値の outlier / 赤セル = 12 ペア中の IQR×1.5 outlier</p>
{summary_table}

<h2>特徴量比較 (12 ペア横並び)</h2>
<div class="plot-grid">
{plot_html}
</div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="12 ペア集約 HTML")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    features_list = screening_utils.load_all_features(args.data_dir)
    if not features_list:
        print(f"error: {args.data_dir} に features.json が見つかりません", file=sys.stderr)
        return 2

    pairs = [f.pair_id for f in features_list]

    metric_defs = [
        ("baseline_median_mA", "Baseline median (mA)"),
        ("baseline_p95_mA", "Baseline p95 (mA)"),
        ("global_mean_mA", "Global mean (mA)"),
        ("global_max_mA", "Global max (mA)"),
        ("wifi_on_mean_mA", "WiFi ON mean (mA)"),
        ("high_current_mean_mA", "High current mean (mA)"),
    ]

    outlier_map: dict[str, list[bool]] = {}
    figures_html: list[str] = []
    for key, label in metric_defs:
        values = [getattr(f, key) for f in features_list]
        lo, hi, flags = iqr_outliers(values)
        outlier_map[key] = flags
        fig = bar_figure(pairs, values, flags, lo, hi, label, label)
        figures_html.append(fig.to_html(
            include_plotlyjs="inline" if len(figures_html) == 0 else False,
            full_html=False,
            config={"displaylogo": False},
        ))

    # 絶対閾値 outlier
    abs_outliers: dict[int, list[str]] = {}
    for f in features_list:
        is_out, reasons = screening_utils.outlier_verdict(f)
        if is_out:
            abs_outliers[f.pair_id] = reasons

    # 相対 outlier サマリ
    relative_outliers: dict[int, list[str]] = {}
    for i, f in enumerate(features_list):
        rs: list[str] = []
        for key, label in metric_defs:
            if outlier_map[key][i]:
                v = getattr(f, key)
                rs.append(f"{label}={v:.3f} は IQR×1.5 outlier")
        if rs:
            relative_outliers[f.pair_id] = rs

    all_outlier_pids = sorted(set(abs_outliers) | set(relative_outliers))
    if all_outlier_pids:
        items = []
        for pid in all_outlier_pids:
            items.append(f"<li><strong>pair{pid:02d}</strong>:")
            reasons_html = []
            if pid in abs_outliers:
                for r in abs_outliers[pid]:
                    reasons_html.append(f"<li>[絶対] {html.escape(r)}</li>")
            if pid in relative_outliers:
                for r in relative_outliers[pid]:
                    reasons_html.append(f"<li>[相対] {html.escape(r)}</li>")
            items.append(f'<ul>{"".join(reasons_html)}</ul></li>')
        verdict_html = f'<div class="verdicts warn"><strong>⚠ outlier あり ({len(all_outlier_pids)} pair)</strong><ul>{"".join(items)}</ul></div>'
    else:
        verdict_html = '<div class="verdicts ok"><strong>✓ outlier なし</strong> — 全 pair が絶対閾値内 & IQR×1.5 内</div>'

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = HTML_TEMPLATE.format(
        ts=ts_str,
        pair_count=len(features_list),
        verdict_html=verdict_html,
        summary_table=render_summary_table(features_list, outlier_map, abs_outliers),
        plot_html="\n".join(figures_html),
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"overview_{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_body)

    print(f"[report] {report_path}", flush=True)
    print(json.dumps({
        "pair_count": len(features_list),
        "abs_outlier_pids": sorted(abs_outliers.keys()),
        "relative_outlier_pids": sorted(relative_outliers.keys()),
        "report": str(report_path),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
