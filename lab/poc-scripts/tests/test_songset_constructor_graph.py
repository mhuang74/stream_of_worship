import json
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

from poc.songset_constructor.graph import _format_llm_prompt_trace, run_constructor
from poc.songset_constructor.models import ConstructorConfig, LlmDraft, SongCandidate


def fixture_pool(config: ConstructorConfig) -> list[SongCandidate]:
    rows = [
        ("s1", "赞美之歌", "h1", 124, "C", "major", "PW", "Alice", "A"),
        ("s2", "献上感恩", "h2", 108, "G", "major", "PW", "Bob", "B"),
        ("s3", "深深敬拜", "h3", 88, "D", "major", "PW", "Cara", "C"),
        ("s4", "十字架前", "h4", 74, "A", "minor", "PW", "Dan", "D"),
        ("s5", "差遣我们", "h5", 82, "C", "major", "PW", "Eve", "E"),
        ("s6", "儿童诗歌", "h6", 120, "Gb", "major", "CPW", "Kid", "F"),
    ]
    songs = [
        SongCandidate(
            song_id=song_id,
            title=title,
            recording_hash_prefix=hash_prefix,
            bpm=bpm,
            musical_key=key,
            musical_mode=mode,
            album_series=series,
            composer=composer,
            album_name=album,
        )
        for song_id, title, hash_prefix, bpm, key, mode, series, composer, album in rows
    ]
    if not config.include_cpw:
        songs = [song for song in songs if "CPW" not in (song.album_series or "")]
    return songs[: config.pool_limit]


class RepairingPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _state):
        self.calls += 1
        if self.calls == 1:
            return [
                LlmDraft(
                    recording_hashes=["h1", "h2", "h2", "h3", "h4"],
                    rationale="bad duplicate",
                )
            ]
        return [LlmDraft(recording_hashes=["h1", "h2", "h3", "h4", "h5"], rationale="repaired")]


def test_graph_refines_invalid_llm_draft_and_writes_artifacts(tmp_path: Path) -> None:
    planner = RepairingPlanner()
    config = ConstructorConfig(output_dir=str(tmp_path), songs=5, top_k=2)

    state = run_constructor(config, catalog_loader=fixture_pool, planner=planner)

    assert planner.calls == 2
    assert state.final_proposals
    assert state.final_proposals[0].items[-1].recording_hash_prefix == "h5"
    assert (tmp_path / "proposals.json").exists()
    assert (tmp_path / "proposal_report.md").exists()
    assert (tmp_path / "candidate_pool.csv").exists()
    assert (tmp_path / "graph_trace.jsonl").exists()
    proposals = json.loads((tmp_path / "proposals.json").read_text(encoding="utf-8"))
    assert proposals[0]["items"][0]["song_id"] == "s1"


def test_fixture_loader_honors_cpw_exclusion() -> None:
    config = ConstructorConfig(include_cpw=False)
    pool = fixture_pool(config)
    assert all("CPW" not in (song.album_series or "") for song in pool)


def test_llm_prompt_trace_formats_full_rendered_messages() -> None:
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You assemble Chinese worship songsets."),
            ("human", "Need {songs} songs. Pool JSON: {pool}. Prior drafts/feedback: {feedback}."),
        ]
    )
    messages = prompt.format_messages(
        songs=5,
        pool=[
            {
                "hash": "h1",
                "title": "赞美之歌",
                "bpm": 124,
                "key": "C",
                "mode": "major",
                "themes": ["praise"],
                "phase": "Praise",
            }
        ],
        feedback=["Unknown recording hashes: nope"],
    )

    trace = _format_llm_prompt_trace(messages)

    assert "[SYSTEM]" in trace
    assert "You assemble Chinese worship songsets." in trace
    assert "[HUMAN]" in trace
    assert "Need 5 songs." in trace
    assert "赞美之歌" in trace
    assert "Unknown recording hashes: nope" in trace
