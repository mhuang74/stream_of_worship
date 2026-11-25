import pytest

from sow_render_worker.simdps_audio import latent_window, search_candidates
from sow_render_worker.glue_provider import generation_duration_ms
from sow_render_worker.glue_descriptor import milliseconds_to_frames


def test_production_gap_and_poc_use_the_same_window_geometry():
    fill = milliseconds_to_frames(generation_duration_ms(4, 86.13))
    assert fill == 132300
    assert latent_window(264600, fill, 264180) == (130, 245)
    assert latent_window(264600, 122880, 264180) == (130, 240)


def test_window_edges_and_source_viability_are_independent():
    with pytest.raises(ValueError):
        latent_window(37000, 132300, 264180)
    window = latent_window(264600, 132300, 264180)
    assert search_candidates(222264, window) == 1
    assert search_candidates(222263, window) == 0
