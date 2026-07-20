from poc.songset_constructor.artifacts.writer import build_review_report, write_artifacts
from poc.songset_constructor.artifacts.writer import (
    _bottleneck_lines,
    _deterministic_arc_narrative,
    _diversity_metrics,
    _diversity_summary,
    _key_tempo_journey_line,
    _score_warnings_line,
    _song_overlap_matrix,
    _song_sequence_line,
    brief_summary_block,
    generate_brief_summaries,
    write_report,
)
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


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def _item(
    position: int,
    title: str = "赞美主",
    song_id: str = "s1",
    phase: int = 1,
    themes: list[str] | None = None,
    bpm: float | None = 124,
    key: str | None = "G",
    mode: str | None = "maj",
) -> ProposalItem:
    return ProposalItem(
        position=position,
        recording_hash_prefix=f"h{position:03d}",
        song_id=song_id,
        title=title,
        phase=phase,
        themes=themes if themes is not None else ["赞美"],
        bpm=bpm,
        key=key,
        mode=mode,
    )


def _proposal_with_items(items: list[ProposalItem], rank: int = 1) -> SongsetProposal:
    return SongsetProposal(
        rank=rank,
        items=items,
        score=ScoreBreakdown(
            f_theme=0.850,
            f_tempo=0.720,
            f_harmony=0.910,
            f_diversity=0.670,
            total=0.7800,
        ),
        rationale="Balanced praise-to-thanksgiving flow.",
        hard_constraint_warnings=["H4 relaxed"],
    )


def test_song_sequence_line():
    proposal = _proposal_with_items(
        [_item(1, "主你荣耀"), _item(2, "恩典已降临"), _item(3, "耶稣我爱祢")]
    )
    assert _song_sequence_line(proposal) == "1. 主你荣耀  →  2. 恩典已降临  →  3. 耶稣我爱祢"


def test_song_sequence_line_single():
    proposal = _proposal_with_items([_item(1, "唯一")])
    assert _song_sequence_line(proposal) == "1. 唯一"


def test_song_sequence_line_empty():
    proposal = _proposal_with_items([])
    assert _song_sequence_line(proposal) == "(no songs)"


def test_key_tempo_journey_line_all_present():
    proposal = _proposal_with_items(
        [_item(1, key="C", mode="maj", bpm=76), _item(2, key="G", mode="maj", bpm=110)]
    )
    assert _key_tempo_journey_line(proposal) == "C maj → G maj  |  76 → 110 BPM arc"


def test_key_tempo_journey_line_missing_key():
    proposal = _proposal_with_items(
        [_item(1, key=None, mode="maj", bpm=76), _item(2, key="G", mode="maj", bpm=110)]
    )
    assert _key_tempo_journey_line(proposal) == "? → G maj  |  76 → 110 BPM arc"


def test_key_tempo_journey_line_missing_bpm():
    proposal = _proposal_with_items(
        [_item(1, key="C", mode="maj", bpm=None), _item(2, key="G", mode="maj", bpm=110)]
    )
    assert _key_tempo_journey_line(proposal) == "C maj → G maj  |  ? → 110 BPM arc"


def test_key_tempo_journey_line_both_missing():
    proposal = _proposal_with_items(
        [_item(1, key=None, mode=None, bpm=None), _item(2, key="G", mode="maj", bpm=110)]
    )
    assert _key_tempo_journey_line(proposal) == "? → G maj  |  ? → 110 BPM arc"


def test_score_warnings_line_with_warnings():
    proposal = _proposal_with_items([_item(1)])
    line = _score_warnings_line(proposal)
    assert "f_theme 0.850" in line
    assert "f_tempo 0.720" in line
    assert "f_harmony 0.910" in line
    assert "f_diversity 0.670" in line
    assert "Warnings: H4 relaxed" in line


def test_score_warnings_line_without_warnings():
    proposal = _proposal_with_items([_item(1)])
    proposal.hard_constraint_warnings = []
    line = _score_warnings_line(proposal)
    assert "Warnings: none" in line


def test_deterministic_arc_narrative_1_3_5():
    proposal = _proposal_with_items(
        [
            _item(1, phase=1, themes=["赞美"]),
            _item(2, phase=3, themes=["敬拜"]),
            _item(3, phase=5, themes=["差遣"]),
        ]
    )
    narrative = _deterministic_arc_narrative(proposal)
    assert "Phase 1 → 3 → 5" in narrative
    assert "call → worship → commitment" in narrative
    assert "赞美" in narrative
    assert "敬拜" in narrative
    assert "差遣" in narrative


def test_deterministic_arc_narrative_1_4():
    proposal = _proposal_with_items(
        [_item(1, phase=1, themes=["赞美"]), _item(2, phase=4, themes=["奉献"])]
    )
    narrative = _deterministic_arc_narrative(proposal)
    assert "Phase 1 → 4" in narrative
    assert "call → response" in narrative


def test_deterministic_arc_narrative_2_3_4_5():
    proposal = _proposal_with_items(
        [
            _item(1, phase=2, themes=["感恩"]),
            _item(2, phase=3, themes=["敬拜"]),
            _item(3, phase=4, themes=["认罪"]),
            _item(4, phase=5, themes=["差遣"]),
        ]
    )
    narrative = _deterministic_arc_narrative(proposal)
    assert "Phase 2 → 3 → 4 → 5" in narrative
    assert "thanksgiving → worship → response → commitment" in narrative


def test_deterministic_arc_narrative_single_phase():
    proposal = _proposal_with_items([_item(1, phase=3, themes=["敬拜"])])
    narrative = _deterministic_arc_narrative(proposal)
    assert "Phase 3" in narrative
    assert "worship" in narrative


# ---------------------------------------------------------------------------
# brief_summary_block
# ---------------------------------------------------------------------------


def test_brief_summary_block_deterministic(synthetic_pool):
    proposal = _proposal_with_items(
        [_item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj")]
    )
    config = RunConfig(no_llm=True, thread_id="test-block-det")
    block = brief_summary_block(proposal, config=config, pool=synthetic_pool)
    assert len(block) == 6
    assert block[0] == "> **Brief Summary**"
    assert block[1].startswith("> Songs:")
    assert block[2].startswith("> Arc:")
    assert "Phase 1" in block[2]
    assert block[3].startswith("> Journey:")
    assert block[4].startswith("> Score:")
    assert block[5].startswith("> Rationale:")


def test_brief_summary_block_with_llm(synthetic_pool):
    proposal = _proposal_with_items(
        [_item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj")]
    )
    config = RunConfig(no_llm=True, thread_id="test-block-llm")
    llm_text = "Opens with an uplifting call, settles into intimate adoration."
    block = brief_summary_block(
        proposal, config=config, pool=synthetic_pool, llm_narrative=llm_text
    )
    assert block[2] == f"> Arc: {llm_text}"
    assert block[1].startswith("> Songs:")
    assert block[3].startswith("> Journey:")
    assert block[4].startswith("> Score:")
    assert block[5].startswith("> Rationale:")


# ---------------------------------------------------------------------------
# generate_brief_summaries
# ---------------------------------------------------------------------------


def test_generate_brief_summaries_no_llm(synthetic_pool):
    proposals = [_proposal(), _proposal()]
    config = RunConfig(no_llm=True, thread_id="test-no-llm")
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 2
    assert all("Phase" in n for n in narratives)


def test_generate_brief_summaries_single_proposal(synthetic_pool):
    proposals = [_proposal()]
    config = RunConfig(no_llm=False, thread_id="test-single", llm_model="test-model")
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 1
    assert "Phase" in narratives[0]


def test_generate_brief_summaries_llm_success(monkeypatch, synthetic_pool):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    proposals = [
        _proposal_with_items(
            [_item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj")]
        ),
        _proposal_with_items(
            [_item(1, "感恩的心", "s2", 2, ["感恩"], 112, "D", "maj")]
        ),
        _proposal_with_items(
            [_item(1, "跟随主", "s3", 5, ["跟随"], 78, "B", "min")]
        ),
    ]
    config = RunConfig(no_llm=False, thread_id="test-llm-success")

    class FakeChat:
        def invoke(self, prompt):
            assert "---PROPOSAL 1---" in prompt
            assert "---PROPOSAL 2---" in prompt
            assert "---PROPOSAL 3---" in prompt
            return (
                "<<<SUMMARY 1>>>\n"
                "Opens with praise. Settles into worship.\n"
                "<<<END SUMMARY 1>>>\n"
                "<<<SUMMARY 2>>>\n"
                "Thanksgiving theme. Gentle tempo.\n"
                "<<<END SUMMARY 2>>>\n"
                "<<<SUMMARY 3>>>\n"
                "Commitment and following. Reflective close.\n"
                "<<<END SUMMARY 3>>>"
            )

    import poc.songset_constructor.graph.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_chat_model", lambda _config: FakeChat())
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 3
    assert "Opens with praise" in narratives[0]
    assert "Thanksgiving theme" in narratives[1]
    assert "Commitment and following" in narratives[2]


def test_generate_brief_summaries_llm_malformed(monkeypatch, synthetic_pool):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    proposals = [
        _proposal_with_items([_item(1, "赞美主", "s1", 1)]),
        _proposal_with_items([_item(1, "感恩的心", "s2", 2)]),
        _proposal_with_items([_item(1, "跟随主", "s3", 5)]),
    ]
    config = RunConfig(no_llm=False, thread_id="test-llm-malformed")

    class FakeChat:
        def invoke(self, _prompt):
            return "This is garbage without delimiters."

    import poc.songset_constructor.graph.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_chat_model", lambda _config: FakeChat())
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 3
    assert all("Phase" in n for n in narratives)


def test_generate_brief_summaries_llm_wrong_count(monkeypatch, synthetic_pool):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    proposals = [
        _proposal_with_items([_item(1, "赞美主", "s1", 1)]),
        _proposal_with_items([_item(1, "感恩的心", "s2", 2)]),
        _proposal_with_items([_item(1, "跟随主", "s3", 5)]),
    ]
    config = RunConfig(no_llm=False, thread_id="test-llm-wrong-count")

    class FakeChat:
        def invoke(self, _prompt):
            return (
                "<<<SUMMARY 1>>>\nFirst.\n<<<END SUMMARY 1>>>\n"
                "<<<SUMMARY 2>>>\nSecond.\n<<<END SUMMARY 2>>>"
            )

    import poc.songset_constructor.graph.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_chat_model", lambda _config: FakeChat())
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 3
    assert all("Phase" in n for n in narratives)


def test_generate_brief_summaries_llm_exception(monkeypatch, synthetic_pool):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    proposals = [
        _proposal_with_items([_item(1, "赞美主", "s1", 1)]),
        _proposal_with_items([_item(1, "感恩的心", "s2", 2)]),
    ]
    config = RunConfig(no_llm=False, thread_id="test-llm-exception")

    class BrokenChat:
        def invoke(self, _prompt):
            raise RuntimeError("boom")

    import poc.songset_constructor.graph.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_chat_model", lambda _config: BrokenChat())
    narratives = generate_brief_summaries(config, proposals)
    assert len(narratives) == 2
    assert all("Phase" in n for n in narratives)


# ---------------------------------------------------------------------------
# Diversity Summary
# ---------------------------------------------------------------------------


def test_diversity_summary_empty(synthetic_pool):
    assert _diversity_summary([], synthetic_pool) == []


def test_diversity_summary_single(synthetic_pool):
    proposal = _proposal_with_items([_item(1, "赞美主", "s1", 1)])
    assert _diversity_summary([proposal], synthetic_pool) == []


def test_diversity_summary_three_overlapping(synthetic_pool):
    proposals = [
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "感恩的心", "s2", 2, ["感恩"], 112, "D", "maj"),
                _item(3, "跟随主", "s5", 5, ["跟随"], 78, "B", "min"),
            ],
            rank=1,
        ),
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "敬拜你", "s3", 3, ["敬拜"], 98, "A", "maj"),
                _item(3, "跟随主", "s5", 5, ["跟随"], 78, "B", "min"),
            ],
            rank=2,
        ),
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "十字架", "s4", 4, ["十字架"], 86, "E", "min"),
                _item(3, "复兴", "s6", 5, ["复兴"], 82, "F#", "min"),
            ],
            rank=3,
        ),
    ]
    lines = _diversity_summary(proposals, synthetic_pool)
    text = "\n".join(lines)
    assert "## Diversity Summary" in text
    assert "Across 3 proposals (9 song slots total):" in text
    assert "Unique songs" in text
    assert "Unique themes" in text
    assert "Unique composers" in text
    assert "Unique phases" in text
    assert "Middle-song reuse" in text
    assert "### Song Overlap Matrix" in text
    assert "### Song Frequency" in text
    assert "### Theme Coverage" in text
    assert "### Bottlenecks" in text
    assert "赞美主" in text


def test_song_overlap_matrix_symmetric():
    proposals = [
        _proposal_with_items(
            [_item(1, "A", "s1"), _item(2, "B", "s2"), _item(3, "C", "s3")]
        ),
        _proposal_with_items(
            [_item(1, "A", "s1"), _item(2, "D", "s4"), _item(3, "E", "s5")]
        ),
        _proposal_with_items(
            [_item(1, "A", "s1"), _item(2, "B", "s2"), _item(3, "F", "s6")]
        ),
    ]
    matrix = _song_overlap_matrix(proposals)
    assert "R1" in matrix[0]
    assert "R2" in matrix[0]
    assert "R3" in matrix[0]
    assert "—" in matrix[2]
    assert "—" in matrix[3]
    assert "—" in matrix[4]
    cell_12 = matrix[2].split("|")[3].strip()
    cell_21 = matrix[3].split("|")[2].strip()
    assert cell_12 == cell_21


def test_bottleneck_lines_none(synthetic_pool):
    proposals = [
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"]),
                _item(2, "感恩的心", "s2", 2, ["感恩"]),
                _item(3, "敬拜你", "s3", 3, ["敬拜"]),
            ]
        ),
        _proposal_with_items(
            [
                _item(1, "十字架", "s4", 4, ["十字架"]),
                _item(2, "跟随主", "s5", 5, ["跟随"]),
                _item(3, "复兴", "s6", 5, ["复兴"]),
            ]
        ),
    ]
    metrics = _diversity_metrics(proposals, synthetic_pool)
    lines = _bottleneck_lines(metrics, proposals, synthetic_pool)
    assert lines == []


# ---------------------------------------------------------------------------
# End-to-End write_report
# ---------------------------------------------------------------------------


def test_write_report_with_summary(tmp_path, synthetic_pool):
    proposals = [
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "感恩的心", "s2", 2, ["感恩"], 112, "D", "maj"),
                _item(3, "跟随主", "s5", 5, ["跟随"], 78, "B", "min"),
            ],
            rank=1,
        ),
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "敬拜你", "s3", 3, ["敬拜"], 98, "A", "maj"),
                _item(3, "复兴", "s6", 5, ["复兴"], 82, "F#", "min"),
            ],
            rank=2,
        ),
        _proposal_with_items(
            [
                _item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj"),
                _item(2, "十字架", "s4", 4, ["十字架"], 86, "E", "min"),
                _item(3, "跟随主", "s5", 5, ["跟随"], 78, "B", "min"),
            ],
            rank=3,
        ),
    ]
    config = RunConfig(no_llm=True, output_dir=tmp_path, thread_id="test-report")
    report_path = tmp_path / "proposal_report.md"
    write_report(report_path, config=config, proposals=proposals, pool=synthetic_pool)
    content = report_path.read_text(encoding="utf-8")
    assert "> **Brief Summary**" in content
    assert "> Songs:" in content
    assert "> Arc:" in content
    assert "> Journey:" in content
    assert "> Score:" in content
    assert "### Details" in content
    assert "## Diversity Summary" in content


def test_write_report_returns_narratives(tmp_path, synthetic_pool):
    proposals = [
        _proposal_with_items([_item(1, "赞美主", "s1", 1)]),
        _proposal_with_items([_item(1, "感恩的心", "s2", 2)]),
    ]
    config = RunConfig(no_llm=True, output_dir=tmp_path, thread_id="test-narratives")
    report_path = tmp_path / "proposal_report.md"
    narratives = write_report(
        report_path, config=config, proposals=proposals, pool=synthetic_pool
    )
    assert isinstance(narratives, list)
    assert len(narratives) == len(proposals)
