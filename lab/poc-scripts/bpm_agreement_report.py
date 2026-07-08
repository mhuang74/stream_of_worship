"""Pairwise BPM method agreement report with octave-corrected metrics.

Compares librosa, madmom, and BeatNet BPM estimates across all songs.
Produces an HTML report with embedded charts and summary statistics.
"""

import base64
import io
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_PATH = Path("lab/poc-scripts/output/bpm_comparison_20260708_094258.csv")
OUTPUT_PATH = Path("lab/poc-scripts/output/bpm_agreement_report.html")

METHOD_COLS = {
    "librosa": "librosa_bpm",
    "madmom": "madmom_bpm",
    "beatnet": "beatnet_bpm",
}

TOLERANCE_BANDS = [1, 2, 5, 10]  # BPM
OCTAVE_RANGE = [-2, -1, 0, 1, 2]  # 2^k shifts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_data(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def octave_corrected_diff(a: float, b: float) -> float:
    """Minimum absolute difference considering octave shifts of b."""
    diffs = []
    for k in OCTAVE_RANGE:
        b_shifted = b * (2 ** k)
        diffs.append(abs(a - b_shifted))
    return min(diffs)


def compute_pairwise_metrics(values_a: np.ndarray, values_b: np.ndarray) -> dict:
    raw_diffs = np.abs(values_a - values_b)
    octave_diffs = np.array([octave_corrected_diff(a, b) for a, b in zip(values_a, values_b)])

    # Octave error detection: ratio is close to 2:1 or 1:2
    ratios = values_a / np.where(values_b == 0, 1, values_b)
    octave_error_mask = (np.abs(ratios - 2.0) < 0.15) | (np.abs(ratios - 0.5) < 0.15)

    return {
        "raw_mae": float(np.mean(raw_diffs)),
        "raw_rmse": float(np.sqrt(np.mean(raw_diffs ** 2))),
        "raw_median": float(np.median(raw_diffs)),
        "raw_std": float(np.std(raw_diffs)),
        "raw_min": float(np.min(raw_diffs)),
        "raw_max": float(np.max(raw_diffs)),
        "octave_mae": float(np.mean(octave_diffs)),
        "octave_rmse": float(np.sqrt(np.mean(octave_diffs ** 2))),
        "octave_median": float(np.median(octave_diffs)),
        "octave_std": float(np.std(octave_diffs)),
        "octave_error_rate": float(np.mean(octave_error_mask) * 100),
        "tolerance_agreement": {
            band: float(np.mean(octave_diffs <= band) * 100) for band in TOLERANCE_BANDS
        },
        "raw_diffs": raw_diffs,
        "octave_diffs": octave_diffs,
        "octave_error_mask": octave_error_mask,
    }


# ---------------------------------------------------------------------------
# Chart generators
# ---------------------------------------------------------------------------


def _make_fig(figsize=(6, 5), dpi=150):
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.set_dpi(dpi)
    return fig, ax


def _save_base64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def chart_scatter_matrix(data: list[dict], methods: dict[str, str]) -> str:
    """3x3 scatter matrix with y=x and y=2x reference lines."""
    names = list(methods.keys())
    n = len(names)
    fig, axes = plt.subplots(n, n, figsize=(18, 18), dpi=150)
    fig.supxlabel("Predicted BPM", fontsize=12)
    fig.supylabel("Reference BPM", fontsize=12)

    for i, name_a in enumerate(names):
        for j, name_b in enumerate(names):
            ax = axes[i, j]
            vals_a = [float(r[methods[name_a]]) for r in data]
            vals_b = [float(r[methods[name_b]]) for r in data]

            if i == j:
                ax.hist(vals_a, bins=50, color="steelblue", edgecolor="white", alpha=0.8)
                ax.set_title(f"{name_a} distribution", fontsize=10)
                ax.set_xlabel("")
                ax.set_ylabel("")
            else:
                ax.scatter(vals_b, vals_a, s=8, alpha=0.4, color="steelblue")
                # y=x line
                lims = [0, max(max(vals_a), max(vals_b)) * 1.05]
                ax.plot(lims, lims, "r--", lw=1, label="y=x")
                # y=2x line
                ax.plot(lims, [2 * l for l in lims], "g--", lw=1, alpha=0.5, label="y=2x")
                # y=x/2 line
                ax.plot(lims, [l / 2 for l in lims], "g--", lw=1, alpha=0.5)
                ax.set_title(f"{name_a} vs {name_b}", fontsize=10)
                ax.set_xlim(lims)
                ax.set_ylim(lims)

            if j == 0:
                ax.set_ylabel(name_a)
            if i == n - 1:
                ax.set_xlabel(name_b)

    axes[0, 1].legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    return _save_base64(fig)


def chart_bland_altman(data: list[dict], methods: dict[str, str]) -> str:
    """Bland-Altman plots for each pair."""
    names = list(methods.keys())
    pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]
    ncols = 2
    nrows = (len(pairs) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 5 * nrows), dpi=150)
    axes = np.atleast_2d(axes)

    for idx, (name_a, name_b) in enumerate(pairs):
        ax = axes.flat[idx]
        vals_a = np.array([float(r[METHOD_COLS[name_a]]) for r in data])
        vals_b = np.array([float(r[METHOD_COLS[name_b]]) for r in data])

        mean_vals = (vals_a + vals_b) / 2
        diff_vals = vals_a - vals_b

        bias = np.mean(diff_vals)
        sd = np.std(diff_vals)

        ax.scatter(mean_vals, diff_vals, s=10, alpha=0.4, color="steelblue")
        ax.axhline(bias, color="red", linestyle="--", lw=1.5, label=f"Mean diff = {bias:.1f}")
        ax.axhline(bias + 1.96 * sd, color="orange", linestyle=":", lw=1, label=f"±1.96 SD = {1.96 * sd:.1f}")
        ax.axhline(bias - 1.96 * sd, color="orange", linestyle=":", lw=1)
        ax.axhline(0, color="gray", linestyle="-", lw=0.5, alpha=0.5)
        ax.set_xlabel("Mean BPM", fontsize=10)
        ax.set_ylabel(f"{name_a} − {name_b}", fontsize=10)
        ax.set_title(f"Bland-Altman: {name_a} vs {name_b}", fontsize=11)
        ax.legend(fontsize=8)

    # Hide unused subplots
    for idx in range(len(pairs), nrows * ncols):
        axes.flat[idx].set_visible(False)

    fig.suptitle("Bland-Altman Plots (Raw Differences)", fontsize=14, y=1.01)
    fig.tight_layout()
    return _save_base64(fig)


def chart_box_octave(data: list[dict], methods: dict[str, str]) -> str:
    """Box plot of octave-corrected absolute differences per pair."""
    names = list(methods.keys())
    pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]

    fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
    all_diffs = []
    labels = []

    for name_a, name_b in pairs:
        vals_a = np.array([float(r[METHOD_COLS[name_a]]) for r in data])
        vals_b = np.array([float(r[METHOD_COLS[name_b]]) for r in data])
        diffs = np.array([octave_corrected_diff(a, b) for a, b in zip(vals_a, vals_b)])
        all_diffs.append(diffs)
        labels.append(f"{name_a} vs {name_b}")

    bp = ax.boxplot(all_diffs, patch_artist=True, widths=0.5)
    ax.set_xticklabels(labels)
    colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6"]
    for patch, color in zip(bp["boxes"], colors[:len(pairs)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)

    ax.set_ylabel("Octave-Corrected Absolute Difference (BPM)", fontsize=11)
    ax.set_title("Octave-Corrected BPM Differences by Pair", fontsize=13)
    ax.grid(axis="y", alpha=0.3)

    # Add median labels
    for i, patch in enumerate(bp["boxes"]):
        ydata = bp["medians"][i].get_ydata()
        ax.text(0.5, ydata[0] + 0.3, f"Median: {np.median(all_diffs[i]):.1f}",
                ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    return _save_base64(fig)


def chart_tolerance_bars(data: list[dict], methods: dict[str, str]) -> str:
    """Stacked bar chart: % agreement within tolerance bands per pair."""
    names = list(methods.keys())
    pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]

    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    x = np.arange(len(pairs))
    width = 0.6

    colors = ["#2ecc71", "#27ae60", "#f1c40f", "#e67e22"]
    for i, band in enumerate(TOLERANCE_BANDS):
        belows = []
        for name_a, name_b in pairs:
            vals_a = np.array([float(r[METHOD_COLS[name_a]]) for r in data])
            vals_b = np.array([float(r[METHOD_COLS[name_b]]) for r in data])
            diffs = np.array([octave_corrected_diff(a, b) for a, b in zip(vals_a, vals_b)])
            belows.append(float(np.mean(diffs <= band) * 100))
        ax.bar(x, belows, width, label=f"≤ {band} BPM", color=colors[i], edgecolor="white")

    ax.set_xticks(x)
    ax.set_xticklabels([f"{a} vs {b}" for a, b in pairs], fontsize=10)
    ax.set_ylabel("Percentage of Songs", fontsize=11)
    ax.set_title("Agreement Within Tolerance Bands (Octave-Corrected)", fontsize=13)
    ax.legend(title="Tolerance", fontsize=9)
    ax.set_ylim(0, 105)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    return _save_base64(fig)


def chart_diff_histograms(data: list[dict], methods: dict[str, str]) -> str:
    """Histograms of raw pairwise differences."""
    names = list(methods.keys())
    pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]

    fig, axes = plt.subplots(1, len(pairs), figsize=(18, 6), dpi=150, sharey=True)

    for idx, (name_a, name_b) in enumerate(pairs):
        ax = axes[idx]
        vals_a = np.array([float(r[METHOD_COLS[name_a]]) for r in data])
        vals_b = np.array([float(r[METHOD_COLS[name_b]]) for r in data])
        raw_diffs = np.abs(vals_a - vals_b)
        octave_diffs = np.array([octave_corrected_diff(a, b) for a, b in zip(vals_a, vals_b)])

        ax.hist(raw_diffs, bins=50, alpha=0.5, color="steelblue", label="Raw diff", edgecolor="white")
        ax.hist(octave_diffs, bins=50, alpha=0.5, color="coral", label="Octave-corrected", edgecolor="white")
        ax.axvline(raw_diffs.mean(), color="red", linestyle="--", lw=1, label=f"Raw mean={raw_diffs.mean():.1f}")
        ax.axvline(octave_diffs.mean(), color="purple", linestyle=":", lw=1, label=f"Octave mean={octave_diffs.mean():.1f}")
        ax.set_title(f"{name_a} vs {name_b}", fontsize=11)
        ax.set_xlabel("Absolute Difference (BPM)")
        ax.legend(fontsize=7)
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Distribution of BPM Differences (Raw vs Octave-Corrected)", fontsize=13)
    fig.tight_layout()
    return _save_base64(fig)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    td_style = 'style="padding:4px 12px;border:1px solid #ddd;text-align:right;"'
    th_style = 'style="padding:6px 12px;border:1px solid #ddd;text-align:center;background:#f5f5f5;"'
    th = "".join(f"<th {th_style}>{h}</th>" for h in headers)
    tr_parts = []
    for row in rows:
        cells = "".join(f"<td {td_style}>{v}</td>" for v in row)
        tr_parts.append(f"<tr>{cells}</tr>")
    trs = "".join(tr_parts)
    return f"<table style='border-collapse:collapse;margin:10px 0;font-family:monospace;font-size:13px;'><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def build_html_report(data: list[dict], metrics: dict[str, dict], charts: dict[str, str]) -> str:
    names = list(METHOD_COLS.keys())
    pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]

    # Summary table: metrics per pair
    metric_keys = [
        ("raw_mae", "Raw MAE"),
        ("raw_rmse", "Raw RMSE"),
        ("raw_median", "Raw Median"),
        ("octave_mae", "Octave MAE"),
        ("octave_rmse", "Octave RMSE"),
        ("octave_median", "Octave Median"),
        ("octave_error_rate", "Octave Error %"),
    ]

    summary_rows = []
    for name_a, name_b in pairs:
        pair_key = f"{name_a} vs {name_b}"
        m = metrics[pair_key]
        row = [pair_key]
        for key, _ in metric_keys:
            val = m[key]
            if key == "octave_error_rate":
                row.append(f"{val:.1f}%")
            else:
                row.append(f"{val:.2f}")
        summary_rows.append(row)

    summary_html = _html_table(["Pair"] + [v for _, v in metric_keys], summary_rows)

    # Tolerance table
    tol_rows = []
    for name_a, name_b in pairs:
        pair_key = f"{name_a} vs {name_b}"
        m = metrics[pair_key]
        row = [pair_key]
        for band in TOLERANCE_BANDS:
            row.append(f"{m['tolerance_agreement'][band]:.1f}%")
        tol_rows.append(row)

    tol_html = _html_table(["Pair"] + [f"≤{b} BPM" for b in TOLERANCE_BANDS], tol_rows)

    # Outlier table: top 10 per pair with largest octave-corrected diff
    outlier_rows = []
    for name_a, name_b in pairs:
        pair_key = f"{name_a} vs {name_b}"
        vals_a = np.array([float(r[METHOD_COLS[name_a]]) for r in data])
        vals_b = np.array([float(r[METHOD_COLS[name_b]]) for r in data])
        diffs = np.array([octave_corrected_diff(a, b) for a, b in zip(vals_a, vals_b)])
        raw_diffs = np.abs(vals_a - vals_b)
        top10_idx = np.argsort(diffs)[-10:][::-1]
        for idx in top10_idx:
            r = data[idx]
            outlier_rows.append([
                pair_key.replace(" vs ", "↔"),
                r["title"],
                f"{vals_a[idx]:.1f}",
                f"{vals_b[idx]:.1f}",
                f"{raw_diffs[idx]:.1f}",
                f"{diffs[idx]:.1f}",
            ])

    outlier_html = _html_table(
        ["Pair", "Song", "Method A", "Method B", "Raw Diff", "Octave-Corrected"],
        outlier_rows,
    )

    # Build full HTML
    charts_html = ""
    chart_titles = {
        "scatter_matrix": "Scatter Matrix (with y=x and y=2x reference lines)",
        "bland_altman": "Bland-Altman Plots",
        "box_octave": "Octave-Corrected Differences (Box Plot)",
        "tolerance_bars": "Agreement Within Tolerance Bands",
        "diff_histograms": "Difference Distribution Histograms",
    }

    for key, title in chart_titles.items():
        img = charts[key]
        charts_html += f"""
        <div style="margin:30px 0;">
            <h2 style="font-family:monospace;font-size:16px;color:#333;">{title}</h2>
            <img src="data:image/png;base64,{img}" style="max-width:100%;border:1px solid #ddd;border-radius:4px;">
        </div>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BPM Method Agreement Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; background: #fafafa; color: #333; }}
        h1 {{ font-size: 24px; color: #1a1a1a; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h2 {{ font-size: 18px; color: #444; margin-top: 30px; }}
        .note {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 15px 0; font-size: 14px; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #888; }}
    </style>
</head>
<body>
    <h1>BPM Method Agreement Report</h1>
    <p>Comparing <strong>{', '.join(names)}</strong> across <strong>{len(data)}</strong> songs.</p>

    <div class="note">
        <strong>Note:</strong> No ground-truth BPM is available. This report measures <em>inter-method agreement</em> only.
        Octave-corrected metrics account for double-time/half-time detection errors by considering 2<sup>k</sup> tempo shifts.
    </div>

    <h2>Summary Metrics</h2>
    {summary_html}

    <h2>Agreement Within Tolerance Bands (Octave-Corrected)</h2>
    {tol_html}

    {charts_html}

    <h2>Top 10 Outliers per Pair (Largest Octave-Corrected Difference)</h2>
    {outlier_html}

    <div class="footer">
        Generated from {CSV_PATH.name} · {OUTPUT_PATH.name}
    </div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print(f"Loading data from {CSV_PATH} ...")
    data = load_data(CSV_PATH)
    print(f"  {len(data)} songs loaded.")

    # Compute pairwise metrics
    names = list(METHOD_COLS.keys())
    metrics = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pair_key = f"{names[i]} vs {names[j]}"
            vals_a = np.array([float(r[METHOD_COLS[names[i]]]) for r in data])
            vals_b = np.array([float(r[METHOD_COLS[names[j]]]) for r in data])
            metrics[pair_key] = compute_pairwise_metrics(vals_a, vals_b)
            print(f"  {pair_key}: raw MAE={metrics[pair_key]['raw_mae']:.2f}, "
                  f"octave MAE={metrics[pair_key]['octave_mae']:.2f}, "
                  f"octave errors={metrics[pair_key]['octave_error_rate']:.1f}%")

    # Generate charts
    print("Generating charts ...")
    charts = {
        "scatter_matrix": chart_scatter_matrix(data, METHOD_COLS),
        "bland_altman": chart_bland_altman(data, METHOD_COLS),
        "box_octave": chart_box_octave(data, METHOD_COLS),
        "tolerance_bars": chart_tolerance_bars(data, METHOD_COLS),
        "diff_histograms": chart_diff_histograms(data, METHOD_COLS),
    }

    # Build and save HTML
    html = build_html_report(data, metrics, charts)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"\nReport saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
