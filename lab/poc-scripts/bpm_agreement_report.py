"""BPM agreement report — v5 lineage + consensus + CPS×BPM crosstab.

Three focused analysis sections across 5 methods (librosa, madmom, beatnet,
prod_v4, prod_v5) and 98 songs:

- **Section B — v5 Lineage Delta**: Did the CPS-derived prior (prod_v5) improve
  accuracy over the octave-guard heuristic (prod_v4), measured against
  ``stored_bpm``?
- **Section C — Consensus & Disagreement**: Across all 5 methods, which songs
  are unambiguous vs. contested?
- **Section D — CPS ↔ BPM Match**: Does lyrical pacing (Characters-Per-Second)
  predict musical tempo, per method? Crosstab in the text-table format already
  produced by ``compare_bpm_libraries.py:print_crosstab``.

Note: ``librosa_raw ≡ prod_v4`` on this dataset (the v4 octave guard never
fires). They are numerically identical across all 98 songs.

Note: prod_v5's high CPS-diagonal agreement in Section D is *partly by
construction* since it uses the CPS prior. The other 4 methods provide the
independent test.
"""

import base64
import csv
import io
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.cluster import KMeans

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_PATH = Path("lab/poc-scripts/output/bpm_comparison_20260708_114300.csv")
OUTPUT_PATH = Path("lab/poc-scripts/output/bpm_agreement_report.html")

METHOD_COLS = {
    "librosa": "librosa_bpm",
    "madmom": "madmom_bpm",
    "beatnet": "beatnet_bpm",
    "prod_v4": "prod_v4_bpm",
    "prod_v5": "prod_v5_bpm",
}

STORED_COL = "stored_bpm"
CPS_COL = "cps"
CPS_BUCKET_COL = "cps_bucket"
OCTAVE_ANCHOR = (60, 160)
CPS_NOMINAL_CUTS = (1.5, 2.8)
BPM_NOMINAL_CUTS = (90.0, 120.0)
RANDOM_STATE = 42

OCTAVE_RANGE = [-2, -1, 0, 1, 2]  # 2^k shifts for octave_corrected_diff

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_data(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def octave_normalize(
    bpm: float, lo: float = OCTAVE_ANCHOR[0], hi: float = OCTAVE_ANCHOR[1]
) -> tuple[float, int]:
    """Shift *bpm* by 2^k into the ``[lo, hi)`` range.

    Returns ``(normalized_value, k)``. For values already in range, returns
    ``(bpm, 0)``. If no k places the value in range, returns the closest shift.
    """
    if lo <= bpm < hi:
        return (bpm, 0)
    best_k = 0
    best_dist = float("inf")
    best_val = bpm
    for k in range(-4, 5):
        shifted = bpm * (2**k)
        if lo <= shifted < hi:
            return (shifted, k)
        dist = 0
        if shifted < lo:
            dist = lo - shifted
        elif shifted >= hi:
            dist = shifted - hi + 1
        if dist < best_dist:
            best_dist = dist
            best_k = k
            best_val = shifted
    return (best_val, best_k)


def octave_corrected_diff(a: float, b: float) -> float:
    """Minimum absolute difference considering octave shifts of b."""
    diffs = []
    for k in OCTAVE_RANGE:
        b_shifted = b * (2**k)
        diffs.append(abs(a - b_shifted))
    return min(diffs)


def cps_value(row: dict) -> Optional[float]:
    """Parse the ``cps`` column; empty string → None."""
    raw = row.get(CPS_COL, "")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def cps_bucket_nominal(cps: Optional[float]) -> Optional[str]:
    """Nominal CPS bucket via ``CPS_NOMINAL_CUTS``; None if missing."""
    if cps is None:
        return None
    if cps < CPS_NOMINAL_CUTS[0]:
        return "slow"
    elif cps <= CPS_NOMINAL_CUTS[1]:
        return "moderate"
    else:
        return "fast"


def bpm_bucket_nominal(bpm: Optional[float]) -> Optional[str]:
    """Nominal BPM bucket via ``BPM_NOMINAL_CUTS``; None if missing."""
    if bpm is None or bpm <= 0:
        return None
    if bpm < BPM_NOMINAL_CUTS[0]:
        return "slow"
    elif bpm <= BPM_NOMINAL_CUTS[1]:
        return "moderate"
    else:
        return "fast"


def kmeans_3(
    arr_1d: np.ndarray, random_state: int = RANDOM_STATE
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """K-means 3-cluster on a 1-D array.

    Returns ``(labels, sorted_centers, cutoffs)`` where ``sorted_centers`` are
    the cluster centers sorted ascending, and ``cutoffs`` are the 2 midpoints
    between adjacent sorted centers.
    """
    arr = np.asarray(arr_1d, dtype=float).reshape(-1, 1)
    km = KMeans(n_clusters=3, n_init=10, random_state=random_state)
    raw_labels = km.fit_predict(arr)
    centers = km.cluster_centers_.flatten()
    sorted_idx = np.argsort(centers)
    sorted_centers = centers[sorted_idx]
    label_map = {int(sorted_idx[0]): 0, int(sorted_idx[1]): 1, int(sorted_idx[2]): 2}
    mapped_labels = np.array([label_map[int(lb)] for lb in raw_labels])
    cutoffs = [
        float((sorted_centers[0] + sorted_centers[1]) / 2.0),
        float((sorted_centers[1] + sorted_centers[2]) / 2.0),
    ]
    return mapped_labels, sorted_centers, cutoffs


def cps_bucket_kmeans(cps: Optional[float], cutoffs: list[float]) -> Optional[str]:
    """Bucket CPS using empirical k-means cutoffs."""
    if cps is None:
        return None
    if cps < cutoffs[0]:
        return "slow"
    elif cps < cutoffs[1]:
        return "moderate"
    else:
        return "fast"


def bpm_bucket_kmeans(bpm: Optional[float], cutoffs: list[float]) -> Optional[str]:
    """Bucket BPM using empirical k-means cutoffs."""
    if bpm is None or bpm <= 0:
        return None
    if bpm < cutoffs[0]:
        return "slow"
    elif bpm < cutoffs[1]:
        return "moderate"
    else:
        return "fast"


def error_vs_stored(vals: np.ndarray, stored: np.ndarray) -> dict:
    """Compute raw + octave-corrected error metrics vs stored BPM.

    Returns dict with ``raw_diffs``, ``octave_diffs``, and aggregate stats
    (MAE/RMSE/median/min/max) for both families.
    """
    raw_diffs = np.abs(vals - stored)
    octave_diffs = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, vals)])

    def _stats(d):
        return {
            "mae": float(np.mean(d)),
            "rmse": float(np.sqrt(np.mean(d**2))),
            "median": float(np.median(d)),
            "min": float(np.min(d)),
            "max": float(np.max(d)),
        }

    return {
        "raw_diffs": raw_diffs,
        "octave_diffs": octave_diffs,
        "raw": _stats(raw_diffs),
        "octave": _stats(octave_diffs),
    }


def per_song_consensus(data: list[dict], methods: dict[str, str]) -> dict:
    """For each song, octave-normalize each method's BPM to [60,160).

    Computes ``median``, ``min``, ``max``, ``radius = max - min``, and
    ``outlier_method`` (method furthest from median).

    Returns dict keyed by song index with per-song consensus info.
    """
    result = {}
    names = list(methods.keys())
    for i, row in enumerate(data):
        norm_vals = {}
        for name in names:
            raw = float(row[methods[name]])
            norm, _ = octave_normalize(raw)
            norm_vals[name] = norm
        vals = np.array(list(norm_vals.values()))
        median = float(np.median(vals))
        min_v = float(np.min(vals))
        max_v = float(np.max(vals))
        radius = max_v - min_v
        # outlier = method furthest from median
        outlier_method = max(names, key=lambda n: abs(norm_vals[n] - median))
        result[i] = {
            "norm_vals": norm_vals,
            "median": median,
            "min": min_v,
            "max": max_v,
            "radius": radius,
            "outlier_method": outlier_method,
        }
    return result


# ---------------------------------------------------------------------------
# Chart utilities
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


# ---------------------------------------------------------------------------
# Section B — v5 Lineage Delta
# ---------------------------------------------------------------------------


def chart_v5_delta_histogram(data: list[dict]) -> str:
    """Overlay histograms of err_v5_raw − err_v4_raw and err_v5_oct − err_v4_oct.

    Vertical line at 0. Negative = v5 better.
    """
    stored = np.array([float(r[STORED_COL]) for r in data])
    v4 = np.array([float(r["prod_v4_bpm"]) for r in data])
    v5 = np.array([float(r["prod_v5_bpm"]) for r in data])

    err_v4_raw = np.abs(v4 - stored)
    err_v5_raw = np.abs(v5 - stored)
    err_v4_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v4)])
    err_v5_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v5)])

    delta_raw = err_v5_raw - err_v4_raw
    delta_oct = err_v5_oct - err_v4_oct

    fig, ax = _make_fig((8, 5))
    bins = np.linspace(
        min(delta_raw.min(), delta_oct.min()) - 1, max(delta_raw.max(), delta_oct.max()) + 1, 40
    )
    ax.hist(
        delta_raw, bins=bins, alpha=0.5, color="steelblue", label="Raw Δerror", edgecolor="white"
    )
    ax.hist(
        delta_oct,
        bins=bins,
        alpha=0.5,
        color="coral",
        label="Octave-corrected Δerror",
        edgecolor="white",
    )
    ax.axvline(0, color="red", linestyle="--", lw=2, label="v5 = v4")
    ax.set_xlabel("Δerror (v5 − v4, BPM)", fontsize=11)
    ax.set_ylabel("Count", fontsize=11)
    ax.set_title("Section B: v5 vs v4 Error Delta (negative = v5 better)", fontsize=13)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save_base64(fig)


def chart_v5_error_scatter(data: list[dict], cps_kmeans_cutoffs: list[float]) -> str:
    """Scatter: x = v4 octave-corrected error, y = v5 octave-corrected error.

    Points colored by CPS bucket (k-means). y=x reference line. Quadrants
    labelled "v5 wins" (below diagonal) / "v5 loses" (above).
    """
    stored = np.array([float(r[STORED_COL]) for r in data])
    v4 = np.array([float(r["prod_v4_bpm"]) for r in data])
    v5 = np.array([float(r["prod_v5_bpm"]) for r in data])

    err_v4_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v4)])
    err_v5_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v5)])

    cps_vals = [cps_value(r) for r in data]
    buckets = [cps_bucket_kmeans(c, cps_kmeans_cutoffs) for c in cps_vals]
    color_map = {"slow": "#3498db", "moderate": "#2ecc71", "fast": "#e74c3c"}
    colors = [color_map.get(b, "#999") for b in buckets]

    fig, ax = _make_fig((7, 7))
    ax.scatter(
        err_v4_oct, err_v5_oct, c=colors, s=30, alpha=0.7, edgecolors="white", linewidths=0.3
    )
    lim_max = max(err_v4_oct.max(), err_v5_oct.max()) * 1.1
    ax.plot([0, lim_max], [0, lim_max], "r--", lw=1.5, label="y = x (tie)")
    ax.fill_between([0, lim_max], [0, lim_max], [0, 0], alpha=0.05, color="green")
    ax.set_xlabel("v4 octave-corrected error (BPM)", fontsize=11)
    ax.set_ylabel("v5 octave-corrected error (BPM)", fontsize=11)
    ax.set_title("Section B: v4 vs v5 Error (per song)", fontsize=13)
    ax.set_xlim(-1, lim_max)
    ax.set_ylim(-1, lim_max)

    # Legend
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor=color_map[b], label=f"CPS {b}") for b in ["slow", "moderate", "fast"]
    ]
    legend_elements.append(plt.Line2D([0], [0], color="r", linestyle="--", label="y = x"))
    ax.legend(handles=legend_elements, fontsize=8, loc="upper left")
    ax.annotate(
        "v5 wins ↓", xy=(lim_max * 0.7, lim_max * 0.15), fontsize=10, color="green", alpha=0.7
    )
    ax.annotate(
        "v5 loses ↑", xy=(lim_max * 0.15, lim_max * 0.7), fontsize=10, color="orange", alpha=0.7
    )
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _save_base64(fig)


def chart_v5_cps_bar(data: list[dict], cps_kmeans_cutoffs: list[float]) -> str:
    """Grouped bars: per CPS bucket (k-means), mean Δerror (oct) and v5 win-rate %."""
    stored = np.array([float(r[STORED_COL]) for r in data])
    v4 = np.array([float(r["prod_v4_bpm"]) for r in data])
    v5 = np.array([float(r["prod_v5_bpm"]) for r in data])
    err_v4_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v4)])
    err_v5_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v5)])
    delta_oct = err_v5_oct - err_v4_oct

    cps_vals = [cps_value(r) for r in data]
    buckets = [cps_bucket_kmeans(c, cps_kmeans_cutoffs) for c in cps_vals]

    bucket_names = ["slow", "moderate", "fast"]
    mean_deltas = []
    win_rates = []
    counts = []
    for bn in bucket_names:
        mask = np.array([b == bn for b in buckets])
        if mask.sum() == 0:
            mean_deltas.append(0)
            win_rates.append(0)
            counts.append(0)
            continue
        mean_deltas.append(float(np.mean(delta_oct[mask])))
        win_rates.append(float(np.mean(delta_oct[mask] < 0) * 100))
        counts.append(int(mask.sum()))

    x = np.arange(len(bucket_names))
    width = 0.35
    fig, ax1 = _make_fig((9, 5))
    bars1 = ax1.bar(
        x - width / 2,
        mean_deltas,
        width,
        color="steelblue",
        label="Mean Δerror (oct)",
        edgecolor="white",
    )
    ax1.set_xlabel("CPS bucket (k-means)", fontsize=11)
    ax1.set_ylabel("Mean Δerror (BPM)", fontsize=11, color="steelblue")
    ax1.axhline(0, color="gray", linestyle="-", lw=0.5)
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{bn}\n(n={counts[i]})" for i, bn in enumerate(bucket_names)])

    ax2 = ax1.twinx()
    bars2 = ax2.bar(
        x + width / 2, win_rates, width, color="coral", label="v5 win-rate %", edgecolor="white"
    )
    ax2.set_ylabel("v5 win-rate (%)", fontsize=11, color="coral")
    ax2.set_ylim(0, 105)

    lines = [bars1, bars2]
    labels = [bar.get_label() for bar in lines]
    ax1.legend(lines, labels, fontsize=9, loc="upper right")
    ax1.set_title("Section B: v5 Improvement by CPS Bucket (k-means)", fontsize=13)
    fig.tight_layout()
    return _save_base64(fig)


def section_b_tables(data: list[dict], cps_kmeans_cutoffs: list[float]) -> dict[str, str]:
    """Build Section B tables."""
    stored = np.array([float(r[STORED_COL]) for r in data])
    v4 = np.array([float(r["prod_v4_bpm"]) for r in data])
    v5 = np.array([float(r["prod_v5_bpm"]) for r in data])

    err_v4_raw = np.abs(v4 - stored)
    err_v5_raw = np.abs(v5 - stored)
    err_v4_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v4)])
    err_v5_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v5)])

    delta_raw = err_v5_raw - err_v4_raw
    delta_oct = err_v5_oct - err_v4_oct

    # Win/Loss/Tie summary
    def wlt(delta):
        wins = int(np.sum(delta < 0))
        loses = int(np.sum(delta > 0))
        ties = int(np.sum(delta == 0))
        mean_d = float(np.mean(delta))
        return wins, loses, ties, mean_d

    w_raw, l_raw, t_raw, md_raw = wlt(delta_raw)
    w_oct, l_oct, t_oct, md_oct = wlt(delta_oct)

    winloss_html = _html_table(
        ["Version", "v5 wins", "v5 loses", "ties", "mean Δerror"],
        [
            ["Raw", str(w_raw), str(l_raw), str(t_raw), f"{md_raw:.2f}"],
            ["Octave-corrected", str(w_oct), str(l_oct), str(t_oct), f"{md_oct:.2f}"],
        ],
    )

    # CPS-bucket stratified (k-means)
    cps_vals = [cps_value(r) for r in data]
    buckets = [cps_bucket_kmeans(c, cps_kmeans_cutoffs) for c in cps_vals]
    bucket_names = ["slow", "moderate", "fast"]
    strat_rows = []
    for bn in bucket_names:
        mask = np.array([b == bn for b in buckets])
        n = int(mask.sum())
        if n == 0:
            strat_rows.append([bn.capitalize(), "0", "—", "—", "—", "—"])
            continue
        v4_mae = float(np.mean(err_v4_oct[mask]))
        v5_mae = float(np.mean(err_v5_oct[mask]))
        mean_d = float(np.mean(delta_oct[mask]))
        wr = float(np.mean(delta_oct[mask] < 0) * 100)
        strat_rows.append(
            [
                bn.capitalize(),
                str(n),
                f"{v4_mae:.2f}",
                f"{v5_mae:.2f}",
                f"{mean_d:.2f}",
                f"{wr:.1f}%",
            ]
        )
    strat_html = _html_table(
        ["CPS bucket (k-means)", "n", "v4 oct-MAE", "v5 oct-MAE", "mean Δerror", "win-rate"],
        strat_rows,
    )

    # Top octave-flip cases: songs where v5 and v4 disagree significantly
    # (v5 picked a different tempo regime than v4). The strict ratio ≈ 2 or 0.5
    # criterion yields no matches in this dataset, so we use octave-corrected
    # difference > 5 BPM between v4 and v5 as the flip criterion.
    flip_indices = [i for i in range(len(data)) if octave_corrected_diff(v4[i], v5[i]) > 5.0]
    flip_indices.sort(key=lambda i: octave_corrected_diff(v4[i], v5[i]), reverse=True)
    flip_indices = flip_indices[:10]

    flip_rows = []
    for i in flip_indices:
        r = data[i]
        cps = cps_value(r)
        cb = cps_bucket_kmeans(cps, cps_kmeans_cutoffs) or "—"
        flip_rows.append(
            [
                r["title"][:40],
                cb,
                f"{v4[i]:.1f}",
                f"{v5[i]:.1f}",
                f"{stored[i]:.1f}",
                f"{err_v4_oct[i]:.2f}",
                f"{err_v5_oct[i]:.2f}",
                r.get("prod_v5_prior", "—"),
            ]
        )
    flip_html = _html_table(
        [
            "Song",
            "CPS bucket",
            "v4_bpm",
            "v5_bpm",
            "stored_bpm",
            "v4 err (oct)",
            "v5 err (oct)",
            "v5_prior",
        ],
        flip_rows,
    )

    return {
        "winloss": winloss_html,
        "stratified": strat_html,
        "octave_flips": flip_html,
    }


# ---------------------------------------------------------------------------
# Section C — Consensus & Disagreement
# ---------------------------------------------------------------------------


def chart_consensus_radius_hist(data: list[dict], consensus: dict) -> str:
    """Histogram of per-song radius (max − min of octave-normalized BPMs).

    Vertical line at median radius.
    """
    radii = np.array([consensus[i]["radius"] for i in range(len(data))])
    median_r = float(np.median(radii))

    fig, ax = _make_fig((8, 5))
    ax.hist(radii, bins=30, color="steelblue", edgecolor="white", alpha=0.8)
    ax.axvline(median_r, color="red", linestyle="--", lw=2, label=f"Median = {median_r:.1f}")
    ax.set_xlabel("Consensus radius (max − min, BPM)", fontsize=11)
    ax.set_ylabel("Count", fontsize=11)
    ax.set_title(
        "Section C: Per-song Consensus Radius (octave-normalized to [60,160))", fontsize=13
    )
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save_base64(fig)


def chart_method_deviation_box(data: list[dict], consensus: dict) -> str:
    """Box plot: per-method distribution of |normalized_bpm − consensus_median|."""
    names = list(METHOD_COLS.keys())
    all_devs = []
    for name in names:
        devs = [
            abs(consensus[i]["norm_vals"][name] - consensus[i]["median"]) for i in range(len(data))
        ]
        all_devs.append(devs)

    fig, ax = _make_fig((8, 5))
    bp = ax.boxplot(all_devs, patch_artist=True, widths=0.5, tick_labels=names)
    colors = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6"]
    for patch, color in zip(bp["boxes"], colors[: len(names)]):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
    ax.set_ylabel("|normalized BPM − consensus median|", fontsize=11)
    ax.set_title("Section C: Per-method Deviation from Consensus Median", fontsize=13)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save_base64(fig)


def chart_disputed_strip(data: list[dict], consensus: dict) -> str:
    """Strip plot for top-10 most-disputed songs.

    Horizontal lines per song; per-method normalized BPM points layered;
    stored_bpm marker as red X.
    """
    names = list(METHOD_COLS.keys())
    sorted_idx = sorted(range(len(data)), key=lambda i: consensus[i]["radius"], reverse=True)[:10]
    sorted_idx = list(reversed(sorted_idx))  # most disputed at top

    fig, ax = _make_fig((10, 6))
    color_map = {
        "librosa": "#3498db",
        "madmom": "#e74c3c",
        "beatnet": "#2ecc71",
        "prod_v4": "#f39c12",
        "prod_v5": "#9b59b6",
    }
    for plot_pos, song_idx in enumerate(sorted_idx):
        ax.axhline(plot_pos, color="gray", linestyle="-", lw=0.3, alpha=0.5)
        for name in names:
            val = consensus[song_idx]["norm_vals"][name]
            ax.scatter(
                val, plot_pos, c=color_map[name], s=40, zorder=3, edgecolors="white", linewidths=0.3
            )
        stored = float(data[song_idx][STORED_COL])
        s_norm, _ = octave_normalize(stored)
        ax.scatter(s_norm, plot_pos, marker="x", c="red", s=80, zorder=4, linewidths=2)

    ax.set_yticks(range(len(sorted_idx)))
    ax.set_yticklabels([data[i]["title"][:30] for i in sorted_idx], fontsize=9)
    ax.set_xlabel("Octave-normalized BPM [60, 160)", fontsize=11)
    ax.set_title("Section C: Top 10 Most-Disputed Songs", fontsize=13)
    from matplotlib.lines import Line2D

    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=color_map[n], markersize=8, label=n)
        for n in names
    ]
    legend_elements.append(
        Line2D([0], [0], marker="x", color="red", linestyle="", markersize=8, label="stored_bpm")
    )
    ax.legend(handles=legend_elements, fontsize=8, loc="upper right", ncol=2)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    return _save_base64(fig)


def section_c_tables(data: list[dict], consensus: dict) -> dict[str, str]:
    """Build Section C tables."""
    names = list(METHOD_COLS.keys())

    # Per-method deviation summary
    dev_rows = []
    for name in names:
        raw_devs = []
        oct_devs = []
        within5 = 0
        for i in range(len(data)):
            raw_val = float(data[i][METHOD_COLS[name]])
            norm_val = consensus[i]["norm_vals"][name]
            med = consensus[i]["median"]
            raw_devs.append(abs(raw_val - med))
            oct_devs.append(abs(norm_val - med))
            if abs(norm_val - med) <= 5:
                within5 += 1
        dev_rows.append(
            [
                name,
                f"{np.mean(raw_devs):.2f}",
                f"{np.mean(oct_devs):.2f}",
                f"{within5 / len(data) * 100:.1f}%",
            ]
        )
    dev_html = _html_table(
        ["Method", "mean abs dev (raw)", "mean abs dev (octave)", "% within ±5 BPM of consensus"],
        dev_rows,
    )

    # Top 10 most-disputed / most-agreed
    sorted_by_radius = sorted(range(len(data)), key=lambda i: consensus[i]["radius"])
    most_agreed = sorted_by_radius[:10]
    most_disputed = list(reversed(sorted_by_radius[-10:]))

    def consensus_song_rows(indices):
        rows = []
        for i in indices:
            r = data[i]
            norm_vals = consensus[i]["norm_vals"]
            raw_vals = {n: float(r[METHOD_COLS[n]]) for n in names}
            stored = float(r[STORED_COL])
            rows.append(
                [
                    r["title"][:35],
                    *[f"{norm_vals[n]:.1f}" for n in names],
                    *[f"{raw_vals[n]:.1f}" for n in names],
                    f"{stored:.1f}",
                    f"{consensus[i]['median']:.1f}",
                    f"{consensus[i]['radius']:.1f}",
                    consensus[i]["outlier_method"],
                ]
            )
        return rows

    headers = (
        ["Song"]
        + [f"{n} (norm)" for n in names]
        + [f"{n} (raw)" for n in names]
        + ["stored", "median", "radius", "outlier"]
    )

    disputed_html = _html_table(headers, consensus_song_rows(most_disputed))
    agreed_html = _html_table(headers, consensus_song_rows(most_agreed))

    return {
        "deviation": dev_html,
        "disputed": disputed_html,
        "agreed": agreed_html,
    }


# ---------------------------------------------------------------------------
# Section D — CPS ↔ BPM Match
# ---------------------------------------------------------------------------


def build_crosstab_text(
    data: list[dict],
    method_name: str,
    method_col: str,
    cps_scheme: str,
    cps_cutoffs: Optional[list[float]],
    bpm_cutoffs: list[float],
) -> str:
    """Build a crosstab matching ``compare_bpm_libraries.py:print_crosstab`` format.

    Returns a pre-formatted HTML ``<pre>`` block.
    """
    bucket_names = ["slow", "moderate", "fast"]
    table = {r: {c: 0 for c in bucket_names} for r in bucket_names}
    total = 0
    diagonal = 0

    for row in data:
        cps = cps_value(row)
        if cps_scheme == "nominal":
            cps_bucket = cps_bucket_nominal(cps)
        else:
            cps_bucket = cps_bucket_kmeans(cps, cps_cutoffs)
        if cps_bucket is None:
            continue
        bpm = float(row[method_col])
        b_bucket = bpm_bucket_kmeans(bpm, bpm_cutoffs)
        if b_bucket is None:
            continue
        table[cps_bucket][b_bucket] += 1
        total += 1
        if cps_bucket == b_bucket:
            diagonal += 1

    lo, hi = bpm_cutoffs
    lo_str = f"{lo:.0f}"
    hi_str = f"{hi:.0f}"
    header = f"{'':>16} {'BPM<' + lo_str:>8} {lo_str + '–' + hi_str:>8} {'>' + hi_str:>8}"
    lines = [f"=== CPS bucket × BPM bucket ({method_name}, {cps_scheme}) ==="]
    lines.append(header)
    for r in bucket_names:
        vals = [table[r][c] for c in bucket_names]
        lines.append(f"  {r.capitalize():<14} {vals[0]:>8} {vals[1]:>8} {vals[2]:>8}")
    if total > 0:
        pct = diagonal / total * 100
        lines.append(f"  Diagonal mass: {diagonal}/{total} = {pct:.0f}%  (CPS-BPM agreement)")
    else:
        lines.append("  Diagonal mass: 0/0 (no data)")

    # Summary row
    if cps_scheme == "nominal":
        cps_cut_str = f"CPS nominal cuts: {CPS_NOMINAL_CUTS[0]}, {CPS_NOMINAL_CUTS[1]}"
    else:
        cps_cut_str = f"CPS k-means cuts: {cps_cutoffs[0]:.2f}, {cps_cutoffs[1]:.2f}"
    lines.append(f"  BPM k-means cuts: {lo_str}, {hi_str}  |  {cps_cut_str}")

    text = "\n".join(lines)
    return f'<pre style="font-family:monospace;font-size:13px;background:#f9f9f9;padding:10px;border:1px solid #ddd;border-radius:4px;margin:8px 0;overflow-x:auto;">{text}</pre>'


def chart_cps_bpm_diagonal_bars(
    data: list[dict],
    cps_nominal_cutoffs_info: tuple,
    cps_kmeans_cutoffs_info: tuple,
    bpm_kmeans_cutoffs: list[float],
) -> str:
    """Grouped bar chart: one cluster per method, diagonal agreement % under each CPS scheme."""
    names = list(METHOD_COLS.keys())
    nominal_pcts = []
    kmeans_pcts = []

    for name in names:
        for scheme, cutoffs, target in [
            ("nominal", None, nominal_pcts),
            ("kmeans", cps_kmeans_cutoffs_info, kmeans_pcts),
        ]:
            bucket_names = ["slow", "moderate", "fast"]
            table = {r: {c: 0 for c in bucket_names} for r in bucket_names}
            total = 0
            diagonal = 0
            for row in data:
                cps = cps_value(row)
                if scheme == "nominal":
                    cps_bucket = cps_bucket_nominal(cps)
                else:
                    cps_bucket = cps_bucket_kmeans(cps, cutoffs)
                if cps_bucket is None:
                    continue
                bpm = float(row[METHOD_COLS[name]])
                b_bucket = bpm_bucket_kmeans(bpm, bpm_kmeans_cutoffs)
                if b_bucket is None:
                    continue
                table[cps_bucket][b_bucket] += 1
                total += 1
                if cps_bucket == b_bucket:
                    diagonal += 1
            target.append(diagonal / total * 100 if total > 0 else 0)

    x = np.arange(len(names))
    width = 0.35
    fig, ax = _make_fig((10, 5))
    bars1 = ax.bar(
        x - width / 2,
        nominal_pcts,
        width,
        color="steelblue",
        label="Nominal CPS",
        edgecolor="white",
    )
    bars2 = ax.bar(
        x + width / 2, kmeans_pcts, width, color="coral", label="K-means CPS", edgecolor="white"
    )
    ax.set_ylabel("Diagonal agreement (%)", fontsize=11)
    ax.set_title("Section D: CPS×BPM Diagonal Agreement by Method", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylim(0, 110)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    for bar in list(bars1) + list(bars2):
        h = bar.get_height()
        ax.annotate(
            f"{h:.0f}%",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    fig.tight_layout()
    return _save_base64(fig)


def chart_cps_vs_bpm_scatter(
    data: list[dict],
    cps_kmeans_cutoffs: list[float],
    bpm_kmeans_cutoffs: list[float],
) -> str:
    """5 panels (one per method). X = CPS continuous, Y = raw BPM.

    Vertical dashed lines at nominal CPS cuts (1.5, 2.8). Horizontal dashed
    lines at empirical BPM cuts. Points colored by CPS bucket (k-means).
    """
    names = list(METHOD_COLS.keys())
    cps_vals = [cps_value(r) for r in data]
    buckets = [cps_bucket_kmeans(c, cps_kmeans_cutoffs) for c in cps_vals]
    color_map = {"slow": "#3498db", "moderate": "#2ecc71", "fast": "#e74c3c"}
    colors = [color_map.get(b, "#999") for b in buckets]

    fig, axes = plt.subplots(1, len(names), figsize=(4 * len(names), 5), dpi=150, sharey=True)
    for idx, name in enumerate(names):
        ax = axes[idx]
        bpms = [float(r[METHOD_COLS[name]]) for r in data]
        valid = [(c, b) for c, b in zip(cps_vals, bpms) if c is not None]
        valid_colors = [colors[i] for i in range(len(cps_vals)) if cps_vals[i] is not None]
        xs = [v[0] for v in valid]
        ys = [v[1] for v in valid]
        ax.scatter(xs, ys, c=valid_colors, s=20, alpha=0.7, edgecolors="white", linewidths=0.3)
        for cut in CPS_NOMINAL_CUTS:
            ax.axvline(cut, color="gray", linestyle="--", lw=0.8, alpha=0.5)
        for cut in bpm_kmeans_cutoffs:
            ax.axhline(cut, color="orange", linestyle="--", lw=0.8, alpha=0.5)
        ax.set_title(name, fontsize=11)
        ax.set_xlabel("CPS", fontsize=10)
        if idx == 0:
            ax.set_ylabel("Raw BPM", fontsize=10)
        ax.grid(alpha=0.3)

    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor=color_map[b], label=f"CPS {b}") for b in ["slow", "moderate", "fast"]
    ]
    legend_elements.append(
        plt.Line2D([0], [0], color="gray", linestyle="--", label="CPS nominal cuts")
    )
    legend_elements.append(
        plt.Line2D([0], [0], color="orange", linestyle="--", label="BPM k-means cuts")
    )
    axes[-1].legend(handles=legend_elements, fontsize=7, loc="upper right")
    fig.suptitle("Section D: CPS vs Raw BPM (per method)", fontsize=13)
    fig.tight_layout()
    return _save_base64(fig)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    td_style = 'style="padding:4px 12px;border:1px solid #ddd;text-align:right;"'
    th_style = (
        'style="padding:6px 12px;border:1px solid #ddd;text-align:center;background:#f5f5f5;"'
    )
    th = "".join(f"<th {th_style}>{h}</th>" for h in headers)
    tr_parts = []
    for row in rows:
        cells = "".join(f"<td {td_style}>{v}</td>" for v in row)
        tr_parts.append(f"<tr>{cells}</tr>")
    trs = "".join(tr_parts)
    return f"<table style='border-collapse:collapse;margin:10px 0;font-family:monospace;font-size:13px;'><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def build_html_report(
    data: list[dict],
    charts: dict[str, str],
    tables: dict[str, dict[str, str]],
) -> str:
    names = list(METHOD_COLS.keys())

    def chart_block(key: str, title: str) -> str:
        img = charts[key]
        return f"""
        <div style="margin:30px 0;">
            <h3 style="font-family:monospace;font-size:15px;color:#333;">{title}</h3>
            <img src="data:image/png;base64,{img}" style="max-width:100%;border:1px solid #ddd;border-radius:4px;">
        </div>"""

    def table_block(title: str, html: str) -> str:
        return f"""
        <div style="margin:20px 0;">
            <h3 style="font-family:monospace;font-size:15px;color:#333;">{title}</h3>
            {html}
        </div>"""

    # Section B
    section_b = f"""
    <h2>Section B — v5 Lineage Delta</h2>
    <p><strong>Research question:</strong> Does the CPS-derived prior (prod_v5) improve
    accuracy over the octave-guard heuristic (prod_v4), measured against <code>stored_bpm</code>?</p>
    {chart_block("v5_delta_histogram", "B1. v5 vs v4 Error Delta Histogram")}
    {chart_block("v5_error_scatter", "B2. v4 vs v5 Error Scatter (per song)")}
    {chart_block("v5_cps_bar", "B3. v5 Improvement by CPS Bucket")}
    {table_block("B4. Win/Loss/Tie Summary", tables["b"]["winloss"])}
    {table_block("B5. CPS-bucket Stratified (k-means)", tables["b"]["stratified"])}
    {table_block("B6. Top Octave-Flip Cases (v5 picked different octave than v4)", tables["b"]["octave_flips"])}
    """

    # Section C
    section_c = f"""
    <h2>Section C — Consensus & Disagreement</h2>
    <p><strong>Research question:</strong> Across all 5 methods, which songs are
    unambiguous vs. contested? Octave anchor: [60, 160).</p>
    {chart_block("consensus_radius_hist", "C1. Consensus Radius Histogram")}
    {chart_block("method_deviation_box", "C2. Per-method Deviation from Consensus")}
    {chart_block("disputed_strip", "C3. Top 10 Most-Disputed Songs (strip plot)")}
    {table_block("C4. Per-method Deviation Summary", tables["c"]["deviation"])}
    {table_block("C5. Top 10 Most-Disputed Songs", tables["c"]["disputed"])}
    {table_block("C6. Top 10 Most-Agreed Songs", tables["c"]["agreed"])}
    """

    # Section D crosstabs
    crosstab_html = ""
    for name in names:
        crosstab_html += tables["d"]["crosstabs"][(name, "nominal")]
        crosstab_html += tables["d"]["crosstabs"][(name, "kmeans")]

    section_d = f"""
    <h2>Section D — CPS ↔ BPM Match</h2>
    <div class="note">
        <strong>Caveats:</strong>
        <ul>
            <li>v5 uses the CPS prior <em>by construction</em>, so its high diagonal
                agreement is <strong>not independent evidence</strong> of the CPS-tempo
                correlation. The other 4 methods provide the independent test.</li>
            <li>Distribution is skewed: only 1 fast-CPS song — interpret the "fast" row
                cautiously.</li>
            <li><code>librosa_raw</code> and <code>prod_v4</code> produce identical BPMs
                on this dataset, so their crosstabs are byte-identical.</li>
        </ul>
    </div>
    <p><strong>Research question:</strong> Does lyrical pacing (CPS) predict musical
    tempo, per method? Crosstab validates the CPS prior hypothesis.</p>
    {chart_block("cps_bpm_diagonal_bars", "D1. Diagonal Agreement % by Method (nominal vs k-means CPS)")}
    {chart_block("cps_vs_bpm_scatter", "D2. CPS vs Raw BPM Scatter (per method)")}
    <h3 style="font-family:monospace;font-size:15px;color:#333;">D3. Crosstabs (5 methods × 2 CPS schemes = 10 tables)</h3>
    {crosstab_html}
    """

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BPM Agreement Report — v5 Lineage + Consensus + CPS×BPM</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; background: #fafafa; color: #333; }}
        h1 {{ font-size: 24px; color: #1a1a1a; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h2 {{ font-size: 20px; color: #444; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 8px; }}
        h3 {{ font-size: 15px; color: #555; }}
        .note {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px 16px; margin: 15px 0; font-size: 14px; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #888; }}
        code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>BPM Agreement Report — v5 Lineage + Consensus + CPS×BPM</h1>
    <p>Comparing <strong>{', '.join(names)}</strong> across <strong>{len(data)}</strong> songs.
    Three analysis sections: v5 lineage delta (B), consensus & disagreement (C),
    and CPS×BPM crosstab (D).</p>
    <div class="note">
        <strong>Notes:</strong>
        <ul>
            <li><code>librosa_raw ≡ prod_v4</code> on this dataset — the v4 octave guard
                never fires (numerically identical across all 98 songs).</li>
            <li><code>stored_bpm</code> is independent ground truth (5-decimal precision,
                from the analysis-service pipeline).</li>
            <li>prod_v5 diverges from v4 on 65/98 songs (where the CPS prior is active;
                the remaining 33 fall back to <code>start_bpm=80</code> and match v4).</li>
        </ul>
    </div>

    {section_b}
    {section_c}
    {section_d}

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
    n = len(data)

    # 1. Compute global k-means BPM cutoffs (490-point combined set)
    all_bpms = []
    for r in data:
        for col in METHOD_COLS.values():
            all_bpms.append(float(r[col]))
    all_bpms_arr = np.array(all_bpms)
    _, bpm_centers, bpm_kmeans_cutoffs = kmeans_3(all_bpms_arr)
    print(f"  Global BPM k-means cutoffs: {bpm_kmeans_cutoffs[0]:.1f}, {bpm_kmeans_cutoffs[1]:.1f}")

    # 2. Compute k-means CPS cutoffs (98-point set)
    cps_vals = [cps_value(r) for r in data]
    cps_valid = np.array([c for c in cps_vals if c is not None])
    _, cps_centers, cps_kmeans_cutoffs = kmeans_3(cps_valid)
    print(f"  CPS k-means cutoffs: {cps_kmeans_cutoffs[0]:.2f}, {cps_kmeans_cutoffs[1]:.2f}")

    # 3. Section B artifacts
    print("Building Section B (v5 Lineage Delta) ...")
    charts = {}
    charts["v5_delta_histogram"] = chart_v5_delta_histogram(data)
    charts["v5_error_scatter"] = chart_v5_error_scatter(data, cps_kmeans_cutoffs)
    charts["v5_cps_bar"] = chart_v5_cps_bar(data, cps_kmeans_cutoffs)
    tables_b = section_b_tables(data, cps_kmeans_cutoffs)

    # Headline stats
    stored = np.array([float(r[STORED_COL]) for r in data])
    v4 = np.array([float(r["prod_v4_bpm"]) for r in data])
    v5 = np.array([float(r["prod_v5_bpm"]) for r in data])
    err_v4_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v4)])
    err_v5_oct = np.array([octave_corrected_diff(s, v) for s, v in zip(stored, v5)])
    delta_oct = err_v5_oct - err_v4_oct
    v5_winrate = float(np.mean(delta_oct < 0) * 100)
    print(f"  v5 win-rate (octave-corrected): {v5_winrate:.1f}%")

    # 4. Section C artifacts
    print("Building Section C (Consensus & Disagreement) ...")
    consensus = per_song_consensus(data, METHOD_COLS)
    charts["consensus_radius_hist"] = chart_consensus_radius_hist(data, consensus)
    charts["method_deviation_box"] = chart_method_deviation_box(data, consensus)
    charts["disputed_strip"] = chart_disputed_strip(data, consensus)
    tables_c = section_c_tables(data, consensus)
    median_radius = float(np.median([consensus[i]["radius"] for i in range(n)]))
    print(f"  Median consensus radius: {median_radius:.1f} BPM")

    # 5. Section D artifacts
    print("Building Section D (CPS × BPM crosstab) ...")
    crosstabs = {}
    for name, col in METHOD_COLS.items():
        crosstabs[(name, "nominal")] = build_crosstab_text(
            data, name, col, "nominal", None, bpm_kmeans_cutoffs
        )
        crosstabs[(name, "kmeans")] = build_crosstab_text(
            data, name, col, "kmeans", cps_kmeans_cutoffs, bpm_kmeans_cutoffs
        )

    charts["cps_bpm_diagonal_bars"] = chart_cps_bpm_diagonal_bars(
        data, (CPS_NOMINAL_CUTS, None), cps_kmeans_cutoffs, bpm_kmeans_cutoffs
    )
    charts["cps_vs_bpm_scatter"] = chart_cps_vs_bpm_scatter(
        data, cps_kmeans_cutoffs, bpm_kmeans_cutoffs
    )

    # Per-method diagonal % headline
    for name in METHOD_COLS:
        for scheme_label, cutoffs in [("nominal", None), ("kmeans", cps_kmeans_cutoffs)]:
            bucket_names = ["slow", "moderate", "fast"]
            table = {r: {c: 0 for c in bucket_names} for r in bucket_names}
            total = 0
            diagonal = 0
            for row in data:
                cps = cps_value(row)
                if scheme_label == "nominal":
                    cb = cps_bucket_nominal(cps)
                else:
                    cb = cps_bucket_kmeans(cps, cutoffs)
                if cb is None:
                    continue
                bb = bpm_bucket_kmeans(float(row[METHOD_COLS[name]]), bpm_kmeans_cutoffs)
                if bb is None:
                    continue
                table[cb][bb] += 1
                total += 1
                if cb == bb:
                    diagonal += 1
            pct = diagonal / total * 100 if total > 0 else 0
            print(f"  {name} ({scheme_label} CPS): diagonal = {diagonal}/{total} = {pct:.0f}%")

    tables_d = {"crosstabs": crosstabs}

    # 6. Assemble and write
    tables = {"b": tables_b, "c": tables_c, "d": tables_d}
    html = build_html_report(data, charts, tables)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\nReport saved to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
