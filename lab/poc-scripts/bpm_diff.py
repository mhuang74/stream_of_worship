import csv
from pathlib import Path

csv_path = Path("lab/poc-scripts/output/bpm_comparison_20260708_094258.csv")

rows = []
with csv_path.open() as f:
    reader = csv.DictReader(f)
    for row in reader:
        beatnet = float(row["beatnet_bpm"])
        v4 = float(row["prod_v4_bpm"])
        rows.append({
            "song_id": row["song_id"],
            "title": row["title"],
            "beatnet": beatnet,
            "v4": v4,
            "diff": abs(beatnet - v4),
        })

rows.sort(key=lambda r: r["diff"], reverse=True)

print(f"{'#':<4} {'song_id':<35} {'Song':<35} {'BeatNet':>8} {'v4':>8} {'Diff':>8}")
print("-" * 76)
for i, r in enumerate(rows, 1):
    print(f"{i:<4} {r['song_id']:<35} {r['title']:<35} {r['beatnet']:>8.1f} {r['v4']:>8.1f} {r['diff']:>8.1f}")
