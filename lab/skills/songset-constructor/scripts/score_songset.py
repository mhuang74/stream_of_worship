#!/usr/bin/env python3
"""Score a proposed songset against fitness functions and validate hard constraints H0-H8.

Usage:
    echo '{"items": [...], "pool": [...], "transitions": [...], "config": {...}}' | python score_songset.py
    python score_songset.py --input draft.json

Input (stdin JSON):
    {
        "items": [
            {"position": 1, "recording_hash_prefix": "a1b2c3d4e5f6", "key_shift_semitones": 0, ...},
            ...
        ],
        "pool": [...],           # enriched SongCandidate objects
        "transitions": [...],    # TransitionCandidate objects from build_transitions
        "config": {
            "count": 4, "intimate": false, "relax_h1": true, "relax_h4": false, ...
        }
    }

Output (stdout JSON):
    {
        "score": {"f_theme": ..., "f_tempo": ..., "f_harmony": ..., "f_diversity": ..., "total": ...},
        "validation": {"passed": ..., "violated": [...], "errors": [...], "repair_hints": [...]},
        "proposal": {...}
    }
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
    parser = argparse.ArgumentParser(description="Score and validate a songset draft")
    parser.add_argument("--input", type=Path, default=None, help="Input JSON file (default: stdin)")
    args = parser.parse_args()

    if args.input:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        data = json.load(sys.stdin)

    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.models import (
        DraftItem,
        SongCandidate,
        SongsetDraft,
        TransitionCandidate,
    )
    from stream_of_worship.admin.songset_constructor.rules.fitness import score
    from stream_of_worship.admin.songset_constructor.rules.hard_constraints import validate
    from stream_of_worship.admin.songset_constructor.rules.proposals import proposal_from_draft

    # Reconstruct pool
    pool = [SongCandidate.model_validate(item) for item in data.get("pool", [])]

    # Reconstruct transition matrix
    transitions_raw = data.get("transitions", [])
    matrix: dict[tuple[str, str], TransitionCandidate] = {}
    for t_data in transitions_raw:
        t = TransitionCandidate.model_validate(t_data)
        matrix[(t.from_hash_prefix, t.to_hash_prefix)] = t

    # Build RunConfig from config dict
    config_dict = data.get("config", {})
    config_kwargs = {}
    valid_fields = set(RunConfig.__dataclass_fields__.keys())
    for key, value in config_dict.items():
        if key in valid_fields and value is not None:
            config_kwargs[key] = value
    config = RunConfig(**config_kwargs)

    # Build draft from items
    items_data = data.get("items", [])
    draft_items = [DraftItem.model_validate(item) for item in items_data]
    draft = SongsetDraft(items=draft_items, rationale=data.get("rationale", ""))

    # Build proposal
    from stream_of_worship.admin.songset_constructor.models import ScoreBreakdown

    placeholder = ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0)
    proposal = proposal_from_draft(draft, pool, placeholder, llm_origin=True)
    proposal = proposal.model_copy(update={"score": score(proposal, config, matrix)})

    # Validate
    feedback = validate(
        proposal,
        config,
        matrix,
        relax_h1=config.relax_h1,
        relax_h4=config.relax_h4,
        relax_h5=config.relax_h5,
    )

    result = {
        "score": proposal.score.model_dump(mode="json"),
        "validation": feedback.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
    }

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
