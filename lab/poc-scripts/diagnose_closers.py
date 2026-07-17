#!/usr/bin/env python3
"""Diagnose why no valid closers exist in the catalog pool."""

from collections import Counter

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.db import fetch_catalog_pool
from poc.songset_constructor.rules.embeddings import load_theme_anchors
from poc.songset_constructor.rules.phases import infer_phase, fuse_themes, apply_seasonal_bias
from poc.songset_constructor.rules.themes import (
    classify_embedding_themes,
    classify_lyrics_themes,
    classify_title_themes,
)


def main() -> None:
    config = RunConfig(env_file=None)
    print(f"closing_limit={config.closing_limit} intimate={config.intimate}")
    pool = fetch_catalog_pool(config)
    anchors = load_theme_anchors()
    enriched = []
    for candidate in pool:
        title = classify_title_themes(candidate.title, candidate.title_pinyin)
        lyrics = classify_lyrics_themes(candidate.lyrics_raw)
        song_emb, line_emb = classify_embedding_themes(
            candidate.song_embedding, candidate.line_embeddings, anchors
        )
        fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
        phase = infer_phase(fused, candidate.tempo_bpm)
        enriched.append((candidate, phase, fused))

    phase_counts = Counter(phase for _, phase, _ in enriched)
    print(f"pool_size={len(enriched)}")
    print(f"phase_counts={dict(sorted(phase_counts.items()))}")

    closers = [
        (c.title, c.tempo_bpm, phase, fused)
        for c, phase, fused in enriched
        if phase in {4, 5} and c.tempo_bpm is not None and c.tempo_bpm <= config.closing_limit
    ]
    print(f"valid_closers_h3 (phase 4/5 & tempo<={config.closing_limit}): {len(closers)}")
    for row in closers[:20]:
        print(f"  {row}")

    print("\n-- phase 4/5 songs (any tempo) --")
    phase45 = [(c.title, c.tempo_bpm, phase) for c, phase, _ in enriched if phase in {4, 5}]
    print(f"count={len(phase45)}")
    for row in phase45[:30]:
        print(f"  {row}")

    print("\n-- tempo <= 90 songs (any phase) --")
    slow = [(c.title, c.tempo_bpm, phase) for c, phase, _ in enriched if c.tempo_bpm is not None and c.tempo_bpm <= 90]
    print(f"count={len(slow)}")
    for row in slow[:30]:
        print(f"  {row}")

    print("\n-- tempo distribution --")
    tempo_buckets = Counter()
    for c, phase, _ in enriched:
        if c.tempo_bpm is None:
            tempo_buckets["None"] += 1
        elif c.tempo_bpm < 70:
            tempo_buckets["<70"] += 1
        elif c.tempo_bpm < 90:
            tempo_buckets["70-89"] += 1
        elif c.tempo_bpm < 100:
            tempo_buckets["90-99"] += 1
        elif c.tempo_bpm < 110:
            tempo_buckets["100-109"] += 1
        elif c.tempo_bpm < 120:
            tempo_buckets["110-119"] += 1
        else:
            tempo_buckets[">=120"] += 1
    print(dict(sorted(tempo_buckets.items())))

    print("\n-- top themes for slowest songs --")
    slow_with_themes = sorted(
        [(c.tempo_bpm, phase, fused, c.title) for c, phase, fused in enriched if c.tempo_bpm is not None],
        key=lambda x: x[0],
    )
    for bpm, phase, fused, title in slow_with_themes[:15]:
        top = sorted(fused.items(), key=lambda kv: -kv[1])[:3]
        print(f"  {bpm:.1f} phase={phase} {title}: {top}")


def test_beam_search() -> None:
    from poc.songset_constructor.rules.beam import _template, compute_fan_out
    from poc.songset_constructor.rules.transitions import recommend_transition
    from poc.songset_constructor.rules.embeddings import load_theme_anchors
    from poc.songset_constructor.rules.phases import infer_phase

    config = RunConfig(env_file=None)
    pool = fetch_catalog_pool(config)
    anchors = load_theme_anchors()
    enriched = []
    for candidate in pool:
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            continue
        title = classify_title_themes(candidate.title, candidate.title_pinyin)
        lyrics = classify_lyrics_themes(candidate.lyrics_raw)
        song_emb, line_emb = classify_embedding_themes(
            candidate.song_embedding, candidate.line_embeddings, anchors
        )
        fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
        enriched.append(
            candidate.model_copy(
                update={"themes": fused, "phase": infer_phase(fused, candidate.tempo_bpm)}
            )
        )

    matrix = {}
    for left in enriched:
        for right in enriched:
            if left.recording_hash_prefix == right.recording_hash_prefix:
                continue
            transition = recommend_transition(left, right)
            if transition.cfd <= 6:
                matrix[(left.recording_hash_prefix, right.recording_hash_prefix)] = transition

    print(f"\n=== beam search tests (pool={len(enriched)}, matrix={len(matrix)}) ===")

    for label, cfg in [
        ("strict", config),
        ("relax_h3=160", RunConfig(**{**config.to_dict(), "relax_h3_bpm": 160, "relax_h4": True, "relax_h5": True})),
    ]:
        from poc.songset_constructor.rules.beam import _candidate_sort_key
        sorted_pool = sorted(compute_fan_out(enriched, matrix, cfg), key=_candidate_sort_key)
        target = _template(cfg.songs)
        print(f"\n  {label}: target={target} closing_limit={cfg.closing_limit} opening_floor={cfg.opening_floor}")
        beams: list[list] = [[]]
        for position, target_phase in enumerate(target, start=1):
            expanded = []
            for beam in beams:
                used = {c.song_id for c in beam}
                for candidate in sorted_pool:
                    if candidate.song_id in used:
                        continue
                    if position == 1:
                        if cfg.relax_h1:
                            if candidate.phase not in {1, 2}:
                                continue
                        elif candidate.phase != 1:
                            continue
                        if candidate.tempo_bpm is None or candidate.tempo_bpm < cfg.opening_floor:
                            continue
                    if position == len(target):
                        if candidate.phase not in {4, 5}:
                            continue
                        if candidate.tempo_bpm is None or candidate.tempo_bpm > cfg.closing_limit:
                            continue
                    if beam and candidate.phase < beam[-1].phase - 1:
                        continue
                    if candidate.is_dead_end and position != len(target):
                        continue
                    if beam:
                        left = beam[-1]
                        transition = matrix.get((left.recording_hash_prefix, candidate.recording_hash_prefix))
                        bpm_delta = transition.bpm_delta if transition else abs((candidate.tempo_bpm or 0) - (left.tempo_bpm or 0))
                        allowed = cfg.h4_limit if (transition and (transition.crossfade_duration_seconds > 0 or transition.gap_beats > 4)) else min(15, cfg.h4_limit)
                        if bpm_delta > allowed:
                            continue
                        distance = transition.cfd if transition else 6
                        shifted_ok = transition is not None and transition.suggested_key_shift != 0
                        if distance > cfg.h5_limit and not shifted_ok:
                            continue
                    expanded.append([*beam, candidate])
            print(f"    position {position} (target phase {target_phase}): beams_in={len(beams)} expanded={len(expanded)}")
            if not expanded:
                print(f"      -> BEAM DIED at position {position}")
                beams = []
                break
            expanded.sort(key=lambda seq: (
                sum(abs((seq[i].phase or 3) - target[i]) for i in range(len(seq))),
                sum(abs((seq[i+1].tempo_bpm or 0) - (seq[i].tempo_bpm or 0)) for i in range(len(seq)-1)),
                tuple(item.recording_hash_prefix for item in seq),
            ))
            beams = expanded[:8]
            if beams:
                sample = [(c.title[:15], c.phase, c.tempo_bpm) for c in beams[0]]
                print(f"      top beam: {sample}")
        print(f"  {label}: final_beams={len(beams)}")


if __name__ == "__main__":
    main()
    test_beam_search()
