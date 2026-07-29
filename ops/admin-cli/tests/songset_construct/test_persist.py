"""Tests for persist_proposals — atomic songset creation."""

from __future__ import annotations

from unittest.mock import MagicMock

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    ProposalItem,
    ScoreBreakdown,
    SongsetProposal,
)
from stream_of_worship.admin.songset_constructor.persist import persist_proposals
from stream_of_worship.db.app.models import Songset
from stream_of_worship.db.app.songset_client import MissingReferenceError


def _make_config() -> RunConfig:
    return RunConfig(count=3, proposals=3, pool=200, use_cache=False)


def _make_proposal(rank: int = 1) -> SongsetProposal:
    return SongsetProposal(
        rank=rank,
        items=[
            ProposalItem(
                position=0,
                recording_hash_prefix="hash001",
                song_id="s1",
                title="Song A",
                phase=1,
                bpm=120.0,
                key="C",
            ),
            ProposalItem(
                position=1,
                recording_hash_prefix="hash002",
                song_id="s2",
                title="Song B",
                phase=3,
                bpm=90.0,
                key="G",
            ),
            ProposalItem(
                position=2,
                recording_hash_prefix="hash003",
                song_id="s3",
                title="Song C",
                phase=5,
                bpm=70.0,
                key="D",
            ),
        ],
        score=ScoreBreakdown(f_theme=0.8, f_tempo=0.7, f_harmony=0.6, f_diversity=0.5, total=0.65),
        rationale="Test proposal",
    )


def _make_songset(songset_id: str = "ss1") -> Songset:
    return Songset(
        id=songset_id,
        user_id=1,
        name="test",
        description="test",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


def test_each_proposal_generates_one_create_call():
    """Each proposal should result in exactly one create_songset_with_items call."""
    config = _make_config()
    proposals = [_make_proposal(rank=i) for i in range(1, 4)]
    mock_client = MagicMock()
    mock_client.create_songset_with_items.side_effect = [
        _make_songset("ss1"),
        _make_songset("ss2"),
        _make_songset("ss3"),
    ]

    created_ids = persist_proposals(config, proposals, mock_client)

    assert len(created_ids) == 3
    assert created_ids == ["ss1", "ss2", "ss3"]
    assert mock_client.create_songset_with_items.call_count == 3


def test_missing_reference_error_on_one_proposal_doesnt_block_others():
    """If one proposal raises MissingReferenceError, others should still be saved."""
    config = _make_config()
    proposals = [_make_proposal(rank=i) for i in range(1, 4)]
    mock_client = MagicMock()
    mock_client.create_songset_with_items.side_effect = [
        _make_songset("ss1"),
        MissingReferenceError("not found", "recording", "hash002"),
        _make_songset("ss3"),
    ]

    created_ids = persist_proposals(config, proposals, mock_client)

    assert len(created_ids) == 2
    assert "ss1" in created_ids
    assert "ss3" in created_ids
    assert mock_client.create_songset_with_items.call_count == 3


def test_persist_returns_list_of_created_songset_ids():
    """persist_proposals should return a list of created songset IDs."""
    config = _make_config()
    proposals = [_make_proposal(rank=1)]
    mock_client = MagicMock()
    mock_client.create_songset_with_items.return_value = _make_songset("ss99")

    created_ids = persist_proposals(config, proposals, mock_client)

    assert isinstance(created_ids, list)
    assert created_ids == ["ss99"]


def test_all_proposals_fail_returns_empty_list():
    """If all proposals fail, return an empty list."""
    config = _make_config()
    proposals = [_make_proposal(rank=i) for i in range(1, 3)]
    mock_client = MagicMock()
    mock_client.create_songset_with_items.side_effect = [
        MissingReferenceError("not found", "recording", "hash001"),
        MissingReferenceError("not found", "recording", "hash002"),
    ]

    created_ids = persist_proposals(config, proposals, mock_client)

    assert created_ids == []


def test_proposal_items_passed_correctly():
    """Verify that proposal items are correctly mapped to the create call."""
    config = _make_config()
    proposal = _make_proposal(rank=1)
    mock_client = MagicMock()
    mock_client.create_songset_with_items.return_value = _make_songset("ss1")

    persist_proposals(config, [proposal], mock_client)

    call_kwargs = mock_client.create_songset_with_items.call_args
    items = call_kwargs.kwargs["items"]
    assert len(items) == 3
    assert items[0]["song_id"] == "s1"
    assert items[0]["recording_hash_prefix"] == "hash001"
    assert items[0]["position"] == 0
    assert items[1]["song_id"] == "s2"
    assert items[1]["position"] == 1
    assert items[2]["song_id"] == "s3"
    assert items[2]["position"] == 2


def test_persisted_positions_are_zero_based():
    """Positions must be 0-based at the persistence boundary (DB convention)."""
    config = _make_config()
    proposal = _make_proposal(rank=1)
    mock_client = MagicMock()
    mock_client.create_songset_with_items.return_value = _make_songset("ss1")

    persist_proposals(config, [proposal], mock_client)

    call_kwargs = mock_client.create_songset_with_items.call_args
    items = call_kwargs.kwargs["items"]
    positions = [item["position"] for item in items]
    assert positions == [0, 1, 2]
