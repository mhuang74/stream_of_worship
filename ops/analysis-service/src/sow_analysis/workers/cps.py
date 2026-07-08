"""Characters-Per-Second (CPS) helpers for prod-v5 BPM prior derivation.

Ported verbatim from ``lab/poc-scripts/compare_bpm_libraries.py:174-283``.

The CPS value is computed from a song's own LRC lyrics and bucketed into
``slow`` / ``moderate`` / ``fast``. Each bucket maps to a lognormal prior
centered on the expected BPM band, which is passed to ``librosa.beat.tempo``
as the ``prior`` argument (replacing the flat ``start_bpm`` scalar from v4).
"""

import math
import unicodedata
from typing import Optional

from scipy import stats

from .lrc_parser import parse_lrc

CPS_SLOW_MAX = 1.5
CPS_MODERATE_MAX = 2.8


def _is_ws_or_punct(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith(("P", "S"))


def count_lyric_chars(text: str) -> int:
    """Count lyric units: CJK characters individually, ASCII alphanumeric runs as 1 token each.

    Whitespace and punctuation/symbol characters are excluded.
    """
    count = 0
    ascii_run = 0
    for ch in text:
        if _is_ws_or_punct(ch):
            if ascii_run:
                count += 1
                ascii_run = 0
            continue
        if ord(ch) > 0x2E7F:  # CJK and surrounding ranges
            if ascii_run:
                count += 1
                ascii_run = 0
            count += 1
        else:  # ASCII letter/digit
            ascii_run += 1
    if ascii_run:
        count += 1
    return count


def compute_cps(lrc_content: str) -> tuple[Optional[float], Optional[dict]]:
    """Compute Characters-Per-Second from LRC content.

    Vocal span = first → last timed LRC line timestamp.
    Returns (cps, meta_dict) or (None, {"reason": ...}) on failure.
    """
    try:
        parsed = parse_lrc(lrc_content)
    except ValueError:
        return None, {"reason": "no valid LRC lines"}
    if len(parsed.lines) < 2:
        return None, {"reason": "fewer than 2 timed lines"}
    total_chars = sum(count_lyric_chars(line.text) for line in parsed.lines)
    span = parsed.lines[-1].time_seconds - parsed.lines[0].time_seconds
    if span <= 0:
        return None, {"reason": "non-positive span"}
    cps = total_chars / span
    return cps, {
        "lines": len(parsed.lines),
        "chars": total_chars,
        "span_s": span,
        "first_ts": parsed.lines[0].time_seconds,
        "last_ts": parsed.lines[-1].time_seconds,
    }


def cps_bucket_label(cps: Optional[float]) -> Optional[str]:
    """Return nominal CPS bucket: 'slow', 'moderate', 'fast', or None."""
    if cps is None:
        return None
    if cps < CPS_SLOW_MAX:
        return "slow"
    elif cps <= CPS_MODERATE_MAX:
        return "moderate"
    else:
        return "fast"


def cps_to_prior(cps: Optional[float]) -> Optional[stats.rv_continuous]:
    """Build a lognormal prior distribution from CPS, or None for fallback.

    When ``cps is None`` (LRC missing), returns None so the caller falls back
    to ``start_bpm=80``.
    """
    if cps is None:
        return None
    if cps < CPS_SLOW_MAX:
        mean, std = 70.0, 12.0
    elif cps <= CPS_MODERATE_MAX:
        mean, std = 105.0, 15.0
    else:
        mean, std = 135.0, 15.0
    var = std**2
    mu = math.log(mean**2 / math.sqrt(var + mean**2))
    sigma = math.sqrt(math.log(1 + var / mean**2))
    return stats.lognorm(scale=math.exp(mu), s=sigma)
