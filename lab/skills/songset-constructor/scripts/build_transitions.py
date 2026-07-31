#!/usr/bin/env python3
"""Compute pairwise transition recommendations for all valid song pairs (CFD ≤ 6).

Also computes fan-out and dead-end status for each song.

Usage:
    cat enriched_pool.json | python build_transitions.py
    python build_transitions.py --input enriched_pool.json

Input: JSON array of enriched SongCandidate objects.
Output: JSON object with "transitions" (list) and "pool" (updated SongCandidate objects) to stdout.
Diagnostics to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build pairwise transition matrix")
    parser.add_argument("--input", type=Path, default=None, help="Input JSON file (default: stdin)")
    args = parser.parse_args()

    if args.input:
        raw_data = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        raw_data = json.load(sys.stdin)

    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.models import SongCandidate
    from stream_of_worship.admin.songset_constructor.rules.beam import compute_fan_out
    from stream_of_worship.admin.songset_constructor.rules.transitions import recommend_transition

    pool = [SongCandidate.model_validate(item) for item in raw_data]

    config = RunConfig()

    matrix: dict[tuple[str, str], object] = {}
    for left in pool:
        for right in pool:
            if left.recording_hash_prefix == right.recording_hash_prefix:
                continue
            transition = recommend_transition(left, right)
            if transition.cfd <= 6:
                matrix[(left.recording_hash_prefix, right.recording_hash_prefix)] = transition

    pool = compute_fan_out(pool, matrix, config)

    transitions_list = [t.model_dump(mode="json") for t in matrix.values()]
    pool_list = [c.model_dump(mode="json") for c in pool]

    # Diagnostics to stderr
    fan_outs = [c.fan_out for c in pool]
    dead_ends = sum(1 for c in pool if c.is_dead_end)
    avg_fan_out = sum(fan_outs) / len(fan_outs) if fan_outs else 0.0

    print(f"Transitions: {len(transitions_list)} valid pairs (CFD ≤ 6)", file=sys.stderr)
    print(f"Fan-out: avg={avg_fan_out:.1f}, max={max(fan_outs) if fan_outs else 0}", file=sys.stderr)
    print(f"Dead-end songs: {dead_ends}/{len(pool)}", file=sys.stderr)

    json.dump({"transitions": transitions_list, "pool": pool_list}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
