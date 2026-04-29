#!/usr/bin/env python3
"""
Batch script to populate songs with audio and LRC data.
Dynamically discovers unpopulated songs from catalog.
"""

import subprocess
import re
import time
import argparse
from datetime import datetime
import json
from pathlib import Path


def run_command(cmd, timeout=300):
    """Run a command and return (success, output, error)"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def get_catalog_ids():
    """Fetch all song IDs from catalog"""
    cmd = "PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main catalog list --format ids"
    success, stdout, stderr = run_command(cmd, timeout=60)

    if not success:
        print(f"Failed to fetch catalog: {stderr}")
        return []

    # Parse lines, filter out warnings and empty lines
    ids = []
    for line in stdout.split('\n'):
        line = line.strip()
        if line and not line.startswith('warning:') and not line.startswith('WARNING:'):
            ids.append(line)

    return ids


def get_audio_ids():
    """Fetch all song IDs that have audio recordings"""
    cmd = "PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main audio list --format ids"
    success, stdout, stderr = run_command(cmd, timeout=60)

    if not success:
        print(f"Failed to fetch audio list: {stderr}")
        return []

    # Parse lines, filter out warnings and empty lines
    ids = []
    for line in stdout.split('\n'):
        line = line.strip()
        if line and not line.startswith('warning:') and not line.startswith('WARNING:'):
            ids.append(line)

    return ids


def get_unpopulated_songs(limit):
    """Get songs from catalog that don't have audio yet"""
    print("Fetching catalog...")
    catalog_ids = get_catalog_ids()
    print(f"  Found {len(catalog_ids)} songs in catalog")

    print("Fetching existing recordings...")
    audio_ids = get_audio_ids()
    print(f"  Found {len(audio_ids)} songs with audio")

    # Compute difference
    audio_set = set(audio_ids)
    unpopulated = [sid for sid in catalog_ids if sid not in audio_set]

    print(f"  {len(unpopulated)} songs without audio")
    print()

    # Return first N
    return unpopulated[:limit]


def extract_job_id(output):
    """Extract job ID from command output"""
    # Look for pattern: LRC job submitted (job: job_XXXXX)
    match = re.search(r'LRC job submitted \(job: (job_[a-f0-9]+)\)', output)
    if match:
        return match.group(1)
    # Alternative pattern: job: job_XXXXX
    match = re.search(r'job: (job_[a-f0-9]+)', output)
    if match:
        return match.group(1)
    return None


def categorize_result(success, output, job_id):
    """Categorize the result of processing a song"""
    combined = output.lower()

    if success and job_id:
        return "newly_processed", "Audio downloaded, LRC job submitted"

    if "already exists" in combined or "already populated" in combined:
        # Extract hash if available
        hash_match = re.search(r'hash[:\s]+([a-f0-9]+)', output)
        if hash_match:
            return "already_exists", f"Already populated (hash: {hash_match.group(1)})"
        return "already_exists", "Already populated"

    if "video is not available" in combined:
        return "youtube_unavailable", "YouTube video not available"

    if "no recordings found" in combined:
        return "no_results", "No YouTube results found"

    if not success:
        return "failed", "Processing failed"

    return "unknown", "Unknown status"


def process_song(song_id, index, total, yes_flag):
    """Process a single song"""
    print(f"\n{'='*60}")
    print(f"Processing song {index}/{total}: {song_id}")
    print(f"{'='*60}")

    result = {
        "song_id": song_id,
        "index": index,
        "start_time": datetime.now().isoformat(),
        "category": "unknown",
        "detail": "",
        "job_id": None,
        "output": ""
    }

    # Build command
    yes_opt = "--yes" if yes_flag else ""
    cmd = f"PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main audio download --lrc {song_id} {yes_opt}"
    cmd = cmd.strip()

    print(f"Running: {cmd}")
    success, stdout, stderr = run_command(cmd, timeout=300)

    combined = stdout + "\n" + stderr
    result["output"] = combined

    # Extract job ID if present
    job_id = extract_job_id(combined)
    if job_id:
        result["job_id"] = job_id

    # Categorize result
    category, detail = categorize_result(success, combined, job_id)
    result["category"] = category
    result["detail"] = detail

    # Display result
    if category == "newly_processed":
        print(f"✅ Success! Job ID: {job_id}")
    elif category == "already_exists":
        print(f"⏭️  {detail}")
    elif category == "youtube_unavailable":
        print(f"❌ YouTube unavailable")
    elif category == "no_results":
        print(f"⚠️  No YouTube results")
    else:
        print(f"❌ Failed: {detail}")

    result["end_time"] = datetime.now().isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Populate songs with audio and LRC data"
    )
    parser.add_argument(
        "-n", "--count",
        type=int,
        default=10,
        help="Number of songs to process (default: 10)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompts"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports",
        help="Directory for reports (default: reports)"
    )
    args = parser.parse_args()

    start_time = datetime.now()

    print("="*60)
    print("SONG POPULATION BATCH PROCESS")
    print("="*60)
    print(f"Start time: {start_time.isoformat()}")
    print(f"Target count: {args.count}")
    print()

    # Get unpopulated songs
    song_ids = get_unpopulated_songs(args.count)

    if not song_ids:
        print("No unpopulated songs found!")
        return

    if len(song_ids) < args.count:
        print(f"Note: Only {len(song_ids)} unpopulated songs available")

    print(f"Will process: {len(song_ids)} songs")
    print()

    results = []
    for i, song_id in enumerate(song_ids, 1):
        result = process_song(song_id, i, len(song_ids), args.yes)
        results.append(result)

        # Delay between requests
        if i < len(song_ids):
            time.sleep(2)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    # Count by category
    categories = {}
    for r in results:
        cat = r["category"]
        categories[cat] = categories.get(cat, 0) + 1

    # Get totals for summary
    total_catalog = len(get_catalog_ids())
    total_with_audio = len(get_audio_ids())
    remaining = total_catalog - total_with_audio - categories.get("newly_processed", 0)

    summary = {
        "batch_info": {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": duration,
            "requested_count": args.count,
            "processed_count": len(song_ids)
        },
        "catalog_stats": {
            "total_songs": total_catalog,
            "with_audio": total_with_audio,
            "without_audio_before": total_catalog - total_with_audio,
            "newly_processed": categories.get("newly_processed", 0),
            "remaining_without_audio": remaining
        },
        "results_by_category": categories,
        "results": results
    }

    # Save reports
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON report
    json_path = output_dir / "populate_songs_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Markdown report
    md_report = generate_markdown_report(summary)
    md_path = output_dir / "populate_songs_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)

    # Final summary
    print(f"\n{'='*60}")
    print("BATCH PROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Processed: {len(song_ids)} songs")
    print(f"  ✅ Newly processed: {categories.get('newly_processed', 0)}")
    print(f"  ⏭️  Already existed: {categories.get('already_exists', 0)}")
    print(f"  ❌ YouTube unavailable: {categories.get('youtube_unavailable', 0)}")
    print(f"  ⚠️  Other failures: {categories.get('failed', 0) + categories.get('no_results', 0)}")
    print()
    print(f"Catalog coverage: {total_with_audio}/{total_catalog} ({(total_with_audio/total_catalog)*100:.1f}%)")
    print(f"Remaining without audio: {remaining}")
    print(f"Duration: {duration/60:.1f} minutes")
    print(f"\nReports saved to:")
    print(f"  - {json_path}")
    print(f"  - {md_path}")


def generate_markdown_report(summary):
    """Generate a markdown report"""
    batch = summary["batch_info"]
    stats = summary["catalog_stats"]
    cats = summary["results_by_category"]

    md = f"""# Song Population Report

## Batch Summary

| Metric | Value |
|--------|-------|
| Start Time | {batch['start_time']} |
| End Time | {batch['end_time']} |
| Duration | {batch['duration_seconds']/60:.1f} minutes |
| Requested | {batch['requested_count']} |
| Processed | {batch['processed_count']} |

## Catalog Statistics

| Metric | Value |
|--------|-------|
| Total Songs | {stats['total_songs']} |
| With Audio (before) | {stats['with_audio']} |
| Without Audio (before) | {stats['without_audio_before']} |
| **Newly Processed** | **{stats['newly_processed']}** |
| Remaining Without Audio | {stats['remaining_without_audio']} |
| Coverage | {(stats['with_audio'] + stats['newly_processed'])/stats['total_songs']*100:.1f}% |

## Results by Category

| Category | Count |
|----------|-------|
| ✅ Newly Processed | {cats.get('newly_processed', 0)} |
| ⏭️ Already Exists | {cats.get('already_exists', 0)} |
| ❌ YouTube Unavailable | {cats.get('youtube_unavailable', 0)} |
| ⚠️ No Results | {cats.get('no_results', 0)} |
| ❌ Failed | {cats.get('failed', 0)} |
| ❓ Unknown | {cats.get('unknown', 0)} |

## Results by Song

| # | Song ID | Category | Detail |
|---|---------|----------|--------|
"""

    for r in summary["results"]:
        cat_emoji = {
            "newly_processed": "✅",
            "already_exists": "⏭️",
            "youtube_unavailable": "❌",
            "no_results": "⚠️",
            "failed": "❌",
            "unknown": "❓"
        }.get(r["category"], "❓")

        detail = r.get("detail", "")
        if len(detail) > 50:
            detail = detail[:47] + "..."

        md += f"| {r['index']} | `{r['song_id']}` | {cat_emoji} | {detail} |\n"

    md += "\n## Newly Processed Songs\n\n"

    newly_processed = [r for r in summary["results"] if r["category"] == "newly_processed"]
    if newly_processed:
        md += "| # | Song ID | Job ID |\n|---|---------|--------|\n"
        for r in newly_processed:
            md += f"| {r['index']} | `{r['song_id']}` | `{r.get('job_id', 'N/A')}` |\n"
    else:
        md += "No songs were newly processed in this batch.\n"

    md += "\n## Failed Songs Details\n\n"

    failed = [r for r in summary["results"] if r["category"] in ("failed", "youtube_unavailable", "no_results")]
    if failed:
        for r in failed:
            md += f"""### {r['index']}. `{r['song_id']}`

- **Category**: {r['category']}
- **Detail**: {r.get('detail', 'N/A')}
- **Time**: {r['start_time']}

<details>
<summary>Output</summary>

```
{r.get('output', 'No output')[:800]}
```

</details>

"""
    else:
        md += "No failed songs.\n"

    return md


if __name__ == "__main__":
    main()
