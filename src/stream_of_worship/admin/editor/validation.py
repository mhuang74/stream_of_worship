"""Validation and quality checks for the admin LRC editor.

Runs quality checks before upload: monotonic timestamps, no unresolved
all-zero draft, duplicate timestamp warnings, duration sanity checks,
and preservation warnings for unsupported LRC content.
"""

import difflib
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCPreservedLine,
    serialize_lrc,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationWarning:
    """A non-blocking quality warning.

    Attributes:
        code: Warning identifier
        message: Human-readable description
    """

    code: str
    message: str


@dataclass
class ValidationError:
    """A blocking validation error that prevents upload.

    Attributes:
        code: Error identifier
        message: Human-readable description
    """

    code: str
    message: str


@dataclass
class ValidationResult:
    """Combined result of LRC validation checks.

    Attributes:
        errors: Blocking errors (upload not allowed)
        warnings: Non-blocking warnings (upload allowed with caution)
        diff: Unified diff between original and revised LRC
    """

    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationWarning] = field(default_factory=list)
    diff: str = ""

    @property
    def can_upload(self) -> bool:
        return len(self.errors) == 0


def validate_lrc(
    timed_lines: List[LRCLine],
    preserved_lines: Optional[List[LRCPreservedLine]] = None,
    original_serialized: Optional[str] = None,
    audio_duration_seconds: Optional[float] = None,
    original_preserved_lines: Optional[List[LRCPreservedLine]] = None,
) -> ValidationResult:
    """Run all quality checks on the revised LRC content.

    Args:
        timed_lines: Revised timed lyric rows
        preserved_lines: Current preserved content
        original_serialized: Force-refreshed original serialized LRC for diff
        audio_duration_seconds: Recording duration for sanity check
        original_preserved_lines: Preserved lines from the original transcribed LRC

    Returns:
        ValidationResult with errors, warnings, and diff
    """
    result = ValidationResult()

    _check_monotonic_timestamps(timed_lines, result)
    _check_all_zero_draft(timed_lines, result)
    _check_duplicate_timestamps(timed_lines, result)
    _check_duration_sanity(timed_lines, audio_duration_seconds, result)
    _check_preservation(original_preserved_lines, preserved_lines, result)

    if original_serialized is not None:
        revised_serialized = serialize_lrc(timed_lines, preserved_lines)
        result.diff = _generate_diff(original_serialized, revised_serialized)

    return result


def _check_monotonic_timestamps(lines: List[LRCLine], result: ValidationResult) -> None:
    """Block upload if timestamps are not monotonic in displayed row order."""
    for i in range(1, len(lines)):
        if lines[i].time_seconds < lines[i - 1].time_seconds:
            result.errors.append(ValidationError(
                code="non_monotonic",
                message=(
                    f"Non-monotonic timestamp at line {i + 1}: "
                    f"{lines[i].time_seconds:.2f}s < {lines[i - 1].time_seconds:.2f}s"
                ),
            ))
            return


def _check_all_zero_draft(lines: List[LRCLine], result: ValidationResult) -> None:
    """Block upload if every non-empty lyric row remains at 00:00.00."""
    non_empty = [line for line in lines if line.text.strip()]
    if non_empty and all(line.time_seconds == 0.0 for line in non_empty):
        result.errors.append(ValidationError(
            code="all_zero_draft",
            message="All non-empty lyric lines remain at 00:00.00. Timestamp at least one line before uploading.",
        ))


def _check_duplicate_timestamps(lines: List[LRCLine], result: ValidationResult) -> None:
    """Warn on duplicate timestamps."""
    seen: dict[float, list[int]] = {}
    for i, line in enumerate(lines):
        rounded = round(line.time_seconds, 2)
        if rounded in seen:
            seen[rounded].append(i + 1)
        else:
            seen[rounded] = [i + 1]

    for ts, line_nums in seen.items():
        if len(line_nums) > 1:
            result.warnings.append(ValidationWarning(
                code="duplicate_timestamp",
                message=f"Duplicate timestamp {ts:.2f}s at lines {', '.join(str(n) for n in line_nums)}",
            ))


def _check_duration_sanity(
    lines: List[LRCLine],
    audio_duration: Optional[float],
    result: ValidationResult,
) -> None:
    """Warn when revised LRC duration is implausibly short or long."""
    if not lines or audio_duration is None or audio_duration <= 0:
        return

    last_ts = lines[-1].time_seconds
    ratio = last_ts / audio_duration

    if last_ts < 10.0:
        result.warnings.append(ValidationWarning(
            code="short_duration",
            message=f"Last timestamp ({last_ts:.1f}s) seems very short for a {audio_duration:.1f}s recording",
        ))
    elif ratio > 1.05:
        result.warnings.append(ValidationWarning(
            code="long_duration",
            message=f"Last timestamp ({last_ts:.1f}s) exceeds audio duration ({audio_duration:.1f}s)",
        ))


def _check_preservation(
    original_preserved: Optional[List[LRCPreservedLine]],
    current_preserved: Optional[List[LRCPreservedLine]],
    result: ValidationResult,
) -> None:
    """Block upload if unknown/malformed transcribed content would be silently dropped.

    Check that all non-empty preserved lines from the original are still
    present in the current state. If any were dropped, that's a blocking
    error unless they were metadata tags (which are always preserved).
    """
    if original_preserved is None:
        return

    current_raw = {p.raw for p in (current_preserved or []) if p.raw.strip()}
    original_raw = {p.raw for p in original_preserved if p.raw.strip()}

    dropped = original_raw - current_raw
    if dropped:
        non_meta_dropped = []
        for raw in dropped:
            is_meta = any(p.tag is not None and p.raw == raw for p in original_preserved)
            if not is_meta:
                non_meta_dropped.append(raw)

        if non_meta_dropped:
            result.errors.append(ValidationError(
                code="content_dropped",
                message=(
                    f"Unknown/malformed transcribed content would be silently dropped: "
                    f"{len(non_meta_dropped)} line(s). Review the diff and explicitly "
                    f"remove lines if intended."
                ),
            ))


def _generate_diff(original: str, revised: str) -> str:
    """Generate a unified diff between original and revised LRC content."""
    orig_lines = original.splitlines(keepends=True)
    rev_lines = revised.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        rev_lines,
        fromfile="original",
        tofile="revised",
    )

    return "".join(diff)
