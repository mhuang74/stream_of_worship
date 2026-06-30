"""Deterministic worship-set music rules used by the POC graph."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from math import inf

from .models import (
    ConstructorConfig,
    Phase,
    ProposalItem,
    ScoreBreakdown,
    SongCandidate,
    SongsetProposal,
    TransitionCandidate,
)


NOTE_TO_PC = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "Fb": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "Cb": 11,
}
PC_TO_KEY = {
    0: "C",
    1: "Db",
    2: "D",
    3: "Eb",
    4: "E",
    5: "F",
    6: "Gb",
    7: "G",
    8: "Ab",
    9: "A",
    10: "Bb",
    11: "B",
}
COF_INDEX = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: 7, 8: 8, 3: 9, 10: 10, 5: 11}

PHASE_ORDER: list[Phase] = ["Praise", "Thanksgiving", "Worship", "Response", "Sending"]
PHASE_TARGETS = {
    "Praise": (110, 140),
    "Thanksgiving": (95, 125),
    "Worship": (75, 100),
    "Response": (60, 85),
    "Sending": (70, 95),
}
PHASE_THEMES = {
    "Praise": {"赞美", "讚美"},
    "Thanksgiving": {"感恩", "感谢", "謝謝", "谢谢"},
    "Worship": {"敬拜", "祈祷", "禱告", "信心", "圣灵", "聖靈"},
    "Response": {"奉献", "奉獻", "认罪", "認罪", "十字架", "降服"},
    "Sending": {"差遣", "跟随", "跟隨", "复兴", "復興", "使命"},
}
THEME_KEYWORDS = {
    "赞美": ["赞美", "讚美", "称颂", "稱頌", "哈利路亚", "歡呼", "欢呼"],
    "感恩": ["感恩", "感谢", "感謝", "恩典", "谢谢", "謝謝"],
    "敬拜": ["敬拜", "敬畏", "荣耀", "榮耀", "圣洁", "聖潔"],
    "奉献": ["奉献", "奉獻", "献上", "獻上", "降服", "摆上", "擺上"],
    "认罪": ["认罪", "認罪", "赦免", "悔改", "洁净", "潔淨"],
    "差遣": ["差遣", "使命", "万国", "萬國", "传扬", "傳揚"],
    "信心": ["信心", "相信", "倚靠", "盼望", "应许", "應許"],
    "祈祷": ["祈祷", "禱告", "呼求", "求你", "医治", "醫治"],
    "复兴": ["复兴", "復興", "更新", "燃烧", "燃燒"],
    "圣灵": ["圣灵", "聖靈", "灵火", "靈火"],
    "十字架": ["十字架", "宝血", "寶血", "救赎", "救贖"],
    "跟随": ["跟随", "跟隨", "道路", "脚步", "腳步"],
}


def parse_key(key: str) -> int:
    normalized = key.strip().replace("♯", "#").replace("♭", "b")
    if normalized.endswith("maj") or normalized.endswith("min"):
        normalized = normalized[:-3]
    if normalized not in NOTE_TO_PC:
        raise ValueError(f"Unsupported key: {key}")
    return NOTE_TO_PC[normalized]


def normalize_mode(mode: str | None) -> str:
    value = (mode or "major").strip().lower()
    if value in {"minor", "min", "m"}:
        return "minor"
    return "major"


def relative_major_pc(pc: int, mode: str | None) -> int:
    return (pc + 3) % 12 if normalize_mode(mode) == "minor" else pc


def fifth_distance_on_circle(a: int, b: int) -> int:
    ia, ib = COF_INDEX[a], COF_INDEX[b]
    return min((ia - ib) % 12, (ib - ia) % 12)


def cfd(key_a: str, mode_a: str | None, key_b: str, mode_b: str | None) -> int:
    pa = relative_major_pc(parse_key(key_a), mode_a)
    pb = relative_major_pc(parse_key(key_b), mode_b)
    return fifth_distance_on_circle(pa, pb)


def key_compatibility_score(distance: int) -> float:
    return max(0.0, 1.0 - distance / 6.0)


def suggest_key_shift(source: SongCandidate, target: SongCandidate) -> tuple[int, int]:
    best_shift = 0
    best_distance = cfd(
        source.musical_key,
        source.musical_mode,
        target.musical_key,
        target.musical_mode,
    )
    target_pc = parse_key(target.musical_key)
    for shift in [0, 1, -1, 2, -2]:
        shifted_key = PC_TO_KEY[(target_pc + shift) % 12]
        distance = cfd(source.musical_key, source.musical_mode, shifted_key, target.musical_mode)
        if distance < best_distance:
            best_distance = distance
            best_shift = shift
    return best_shift, best_distance


def classify_themes(title: str, lyrics: str | None, album_series: str | None = None) -> list[str]:
    text = f"{title}\n{lyrics or ''}"
    scores: Counter[str] = Counter()
    for theme, keywords in THEME_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                scores[theme] += 1
    series = (album_series or "").upper()
    if "DEV" in series:
        scores["敬拜"] += 1
    if "HYMN" in series:
        scores["差遣"] += 1
    if not scores:
        return ["敬拜"]
    return [theme for theme, _ in scores.most_common(3)]


def infer_phase(
    themes: list[str],
    album_series: str | None = None,
    bpm: float | None = None,
) -> Phase:
    series = (album_series or "").upper()
    if "DEV" in series:
        return "Response"
    for phase in PHASE_ORDER:
        if set(themes) & PHASE_THEMES[phase]:
            return phase
    if bpm is not None:
        if bpm >= 110:
            return "Praise"
        if bpm >= 95:
            return "Thanksgiving"
        if bpm >= 80:
            return "Worship"
        return "Response"
    return "Worship"


def enrich_candidate(song: SongCandidate) -> SongCandidate:
    themes = classify_themes(song.title, song.lyrics_raw, song.album_series)
    phase = infer_phase(themes, song.album_series, song.bpm)
    return song.model_copy(update={"inferred_themes": themes, "phase": phase})


def build_transition(source: SongCandidate, target: SongCandidate) -> TransitionCandidate:
    shift, distance = suggest_key_shift(source, target)
    bpm_delta = target.bpm - source.bpm
    warnings: list[str] = []
    if abs(bpm_delta) > 15:
        warnings.append("tempo_delta_gt_15")
    if distance > 2:
        warnings.append("distant_key_requires_vamp")
    if target.key_confidence is not None and target.key_confidence < 0.6 and shift:
        warnings.append("low_key_confidence_for_transposition")
    crossfade = distance > 2 or abs(bpm_delta) > 15
    return TransitionCandidate(
        from_hash=source.recording_hash_prefix,
        to_hash=target.recording_hash_prefix,
        bpm_delta=bpm_delta,
        cfd=distance,
        compatibility_score=key_compatibility_score(distance),
        key_shift_semitones=shift,
        tempo_ratio=max(0.85, min(1.15, source.bpm / target.bpm)),
        gap_beats=8.0 if abs(bpm_delta) > 15 else 2.0,
        crossfade_enabled=crossfade,
        crossfade_duration_seconds=6.0 if crossfade else None,
        warnings=warnings,
    )


def phase_template(song_count: int) -> list[Phase]:
    if song_count == 4:
        return ["Praise", "Thanksgiving", "Worship", "Response"]
    return PHASE_ORDER[:song_count]


def candidate_to_item(song: SongCandidate, position: int) -> ProposalItem:
    return ProposalItem(
        song_id=song.song_id,
        recording_hash_prefix=song.recording_hash_prefix,
        position=position,
        title=song.title,
        phase=song.phase,
    )


def make_proposal(
    songs: list[SongCandidate],
    matrix: dict[str, dict[str, TransitionCandidate]],
    config: ConstructorConfig,
    rationale: str = "",
) -> SongsetProposal:
    items = [candidate_to_item(song, index + 1) for index, song in enumerate(songs)]
    for index in range(1, len(items)):
        transition = matrix[items[index - 1].recording_hash_prefix][
            items[index].recording_hash_prefix
        ]
        items[index].key_shift_semitones = transition.key_shift_semitones
        items[index].tempo_ratio = transition.tempo_ratio
        items[index].gap_beats = transition.gap_beats
        items[index].crossfade_enabled = transition.crossfade_enabled
        items[index].crossfade_duration_seconds = transition.crossfade_duration_seconds
    proposal = SongsetProposal(items=items, llm_rationale=rationale)
    errors, warnings, score = validate_and_score(
        proposal,
        {s.recording_hash_prefix: s for s in songs},
        matrix,
        config,
    )
    proposal.validation_errors = errors
    proposal.validation_warnings = warnings
    proposal.score_breakdown = score
    return proposal


def validate_and_score(
    proposal: SongsetProposal,
    pool_by_hash: dict[str, SongCandidate],
    matrix: dict[str, dict[str, TransitionCandidate]],
    config: ConstructorConfig,
) -> tuple[list[str], list[str], ScoreBreakdown]:
    errors: list[str] = []
    warnings: list[str] = []
    if len(proposal.items) != config.songs:
        errors.append(f"H1 expected {config.songs} songs")
    hashes = [item.recording_hash_prefix for item in proposal.items]
    if len(set(hashes)) != len(hashes):
        errors.append("H2 duplicate recordings")
    missing = [h for h in hashes if h not in pool_by_hash]
    if missing:
        errors.append(f"H3 unknown recordings: {', '.join(missing)}")
        return errors, warnings, ScoreBreakdown()

    songs = [pool_by_hash[h] for h in hashes]
    if any(song.bpm <= 0 or not song.musical_key for song in songs):
        errors.append("H3 unusable BPM/key metadata")
    expected = phase_template(config.songs)
    phase_matches = sum(1 for song, phase in zip(songs, expected) if song.phase == phase)
    if phase_matches < max(2, config.songs - 2):
        warnings.append("H4 weak phase arc")
    if songs and songs[0].bpm < 105:
        warnings.append("H5 opener below praise tempo")
    closing_limit = 80 if config.intimate else 95
    if songs and songs[-1].bpm > closing_limit:
        warnings.append("H6 closer above target tempo")

    tempo_scores: list[float] = []
    harmony_scores: list[float] = []
    upticks = 0
    for left, right in zip(songs, songs[1:]):
        transition = matrix[left.recording_hash_prefix][right.recording_hash_prefix]
        if transition.bpm_delta > 0:
            upticks += 1
        if abs(transition.bpm_delta) > 20:
            errors.append("H7 adjacent tempo delta above 20 BPM")
        if transition.cfd > 2 and not transition.crossfade_enabled:
            errors.append("H8 distant key without transition")
        tempo_scores.append(max(0.0, 1.0 - abs(transition.bpm_delta) / 30.0))
        harmony_scores.append(transition.compatibility_score)
        warnings.extend(transition.warnings)
    if upticks > 1:
        warnings.append("more_than_one_tempo_uptick")

    theme_score = phase_matches / max(1, len(expected))
    tempo_score = sum(tempo_scores) / len(tempo_scores) if tempo_scores else 0.0
    harmony_score = sum(harmony_scores) / len(harmony_scores) if harmony_scores else 0.0
    composers = {song.composer for song in songs if song.composer}
    albums = {song.album_name for song in songs if song.album_name}
    diversity_score = min(1.0, (len(composers) + len(albums)) / max(1, len(songs)))
    total = theme_score * 0.4 + tempo_score * 0.3 + harmony_score * 0.2 + diversity_score * 0.1
    return errors, sorted(set(warnings)), ScoreBreakdown(
        theme=round(theme_score, 4),
        tempo=round(tempo_score, 4),
        harmony=round(harmony_score, 4),
        diversity=round(diversity_score, 4),
        total=round(total, 4),
    )


def beam_seed_candidates(
    pool: list[SongCandidate],
    matrix: dict[str, dict[str, TransitionCandidate]],
    config: ConstructorConfig,
) -> list[SongsetProposal]:
    template = phase_template(config.songs)
    phase_ranked: list[list[SongCandidate]] = []
    for phase in template:
        low, high = PHASE_TARGETS[phase]
        midpoint = (low + high) / 2
        matches = [song for song in pool if song.phase == phase] or pool
        phase_ranked.append(
            sorted(matches, key=lambda song: (abs(song.bpm - midpoint), song.title))[:30]
        )

    beams: list[list[SongCandidate]] = [[]]
    for ranked in phase_ranked:
        next_beams: list[list[SongCandidate]] = []
        for beam in beams:
            used = {song.recording_hash_prefix for song in beam}
            for song in ranked:
                if song.recording_hash_prefix in used:
                    continue
                if beam:
                    transition = matrix[beam[-1].recording_hash_prefix][song.recording_hash_prefix]
                    if abs(transition.bpm_delta) > 25 or transition.cfd == 6:
                        continue
                next_beams.append([*beam, song])
        beams = sorted(
            next_beams,
            key=lambda seq: _sequence_pre_score(seq, matrix, template),
            reverse=True,
        )[:80]

    proposals = [make_proposal(seq, matrix, config, "deterministic beam seed") for seq in beams]
    proposals = [proposal for proposal in proposals if not proposal.validation_errors] or proposals
    return sorted(
        proposals,
        key=lambda proposal: proposal.score_breakdown.total,
        reverse=True,
    )[: config.top_k * 4]


def _sequence_pre_score(
    songs: list[SongCandidate],
    matrix: dict[str, dict[str, TransitionCandidate]],
    template: list[Phase],
) -> float:
    phase_score = sum(1 for song, phase in zip(songs, template) if song.phase == phase)
    transition_score = 0.0
    for left, right in zip(songs, songs[1:]):
        transition = matrix[left.recording_hash_prefix][right.recording_hash_prefix]
        transition_score += transition.compatibility_score - abs(transition.bpm_delta) / 30
    return phase_score + transition_score


def detect_dead_ends(
    pool: list[SongCandidate],
    matrix: dict[str, dict[str, TransitionCandidate]],
) -> list[str]:
    warnings: list[str] = []
    for song in pool:
        fanout = 0
        for other in pool:
            if other.recording_hash_prefix == song.recording_hash_prefix:
                continue
            transition = matrix[song.recording_hash_prefix][other.recording_hash_prefix]
            if abs(transition.bpm_delta) <= 20 and transition.cfd <= 2:
                fanout += 1
        if fanout < 2:
            warnings.append(f"dead_end:{song.recording_hash_prefix}")
    return warnings
