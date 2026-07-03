from poc.songset_constructor.artifacts.writer import build_review_report, write_artifacts
from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import ProposalItem, ScoreBreakdown, SongsetProposal


def _proposal(llm_origin: bool = False) -> SongsetProposal:
    return SongsetProposal(
        rank=1,
        items=[
            ProposalItem(
                position=1,
                recording_hash_prefix="h001",
                song_id="s1",
                title="赞美主",
                phase=1,
                themes=["赞美"],
                bpm=124,
                key="G",
                mode="maj",
            ),
            ProposalItem(
                position=2,
                recording_hash_prefix="h002",
                song_id="s2",
                title="感恩的心",
                phase=2,
                themes=["感恩"],
                bpm=112,
                key="D",
                mode="maj",
            ),
        ],
        score=ScoreBreakdown(
            f_theme=0.8,
            f_tempo=0.7,
            f_harmony=0.9,
            f_diversity=0.6,
            total=0.75,
        ),
        rationale="Balanced praise-to-thanksgiving flow.",
        hard_constraint_warnings=["relaxed_h4"],
        llm_origin=llm_origin,
    )


def test_review_report_uses_llm_markdown(synthetic_pool):
    class FakeChat:
        def invoke(self, prompt):
            assert "Factual payload:" in prompt
            assert "赞美主" in prompt
            return (
                "# Songset Constructor Review\n\n"
                "## Key Findings\n\n"
                "- Fake LLM prose mentions 1 proposal.\n\n"
                "## Run Summary\n\n"
                "Run ID: test-thread\n\n"
                "## What Was Done\n\n"
                "Artifacts were prepared.\n\n"
                "## How Filters Were Applied\n\n"
                "relaxed_h4 mattered.\n\n"
                "## Proposal 1\n\n"
                "| # | Title |\n|---|---|\n| 1 | 赞美主 |\n"
            )

    report = build_review_report(
        config=RunConfig(no_llm=False, thread_id="test-thread"),
        proposals=[_proposal(llm_origin=True)],
        pool=synthetic_pool,
        trace=[],
        chat=FakeChat(),
    )

    assert "Fake LLM prose" in report
    assert "## Key Findings" in report
    assert "赞美主" in report


def test_review_report_fallback_no_llm_contains_required_sections(synthetic_pool):
    report = build_review_report(
        config=RunConfig(no_llm=True, thread_id="test-thread", relax_h4=True),
        proposals=[_proposal()],
        pool=synthetic_pool,
        trace=[
            {
                "node": "enrich_pool",
                "event": "exit",
                "iteration": 0,
                "data": {"pool_size": len(synthetic_pool), "dropped": 2},
            }
        ],
    )

    assert report.startswith("# Songset Constructor Review")
    assert "## Key Findings" in report
    assert "## Proposal 1" in report
    assert "| 1 | 赞美主 | 1 | 124 | G maj | 赞美 | shift 0, gap 2 beats |" in report
    assert "Score components: theme 0.800, tempo 0.700, harmony 0.900, diversity 0.600." in report
    assert "relax_h4=True" in report
    assert "relaxed_h4" in report


def test_review_report_falls_back_when_llm_raises(synthetic_pool):
    class BrokenChat:
        def invoke(self, _prompt):
            raise RuntimeError("boom")

    report = build_review_report(
        config=RunConfig(no_llm=False, thread_id="test-thread"),
        proposals=[_proposal()],
        pool=synthetic_pool,
        trace=[],
        chat=BrokenChat(),
    )

    assert "LLM report generation failed; fallback report used." in report
    assert "## Proposal 1" in report
    assert "赞美主" in report


def test_write_artifacts_returns_review_path(tmp_path, synthetic_pool):
    paths = write_artifacts(
        config=RunConfig(no_llm=True, output_dir=tmp_path, thread_id="test-thread"),
        proposals=[_proposal()],
        pool=synthetic_pool,
        trace=[],
    )

    assert paths["review"] == str(tmp_path / "songset_review.md")
    assert (tmp_path / "songset_review.md").exists()
