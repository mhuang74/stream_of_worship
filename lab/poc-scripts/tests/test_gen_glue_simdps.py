"""Offline tests of the SimDPS Glue CLI and the Modal crop contract."""

import json
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import gen_glue_simdps as simdps
from gen_glue_elevenlabs import SR, Recording, SongsetItem
from modal_apps.simdps_inpaint_app import extract_fill


def test_dry_run_has_no_io(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run tried to load environment or connect to a service")

    monkeypatch.setattr(simdps, "execute_run", forbidden)
    monkeypatch.setattr(simdps.glue, "load_env_file", forbidden)
    monkeypatch.setattr(simdps, "modal_inpainter", forbidden)
    assert simdps.main(["--dry-run", "--dry-run-items", "5", "--out-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "122,880 frames" in output
    assert all(f"j{number} ·" in output for number in range(1, 5))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("model_rate", [44100, 48000, 24000])
def test_extract_fill_uses_absolute_crop_coordinates_and_exact_frame_count(model_rate):
    requested = (10101, 13334)
    gap = tuple(round(frame * model_rate / SR) for frame in requested)
    window = (gap[0] - 123, gap[1] + 456)
    raw = np.full((1, 2, window[1] - window[0]), 12, dtype=np.float32)
    raw[0, :, 123:-456] = np.array([[0.1], [-0.2]])

    fill = extract_fill(raw, model_rate, window, gap, requested)

    assert fill.shape == (requested[1] - requested[0], 2)
    assert fill.dtype == np.float32
    np.testing.assert_allclose(fill[30:-30, 0], 0.1, atol=0.001)
    np.testing.assert_allclose(fill[30:-30, 1], -0.2, atol=0.001)
    assert np.max(np.abs(fill)) < 0.25
    with pytest.raises(ValueError, match="unsupported model window"):
        extract_fill(raw[..., :-1], model_rate, window, gap, requested)


def read_audio(path):
    samples, rate = sf.read(path, dtype="float32", always_2d=True)
    assert rate == SR
    return samples


@pytest.mark.parametrize("stem_state", ["present", "missing", "removed", "denied"])
def test_run_keeps_song_sides_exact_and_reports_every_junction(
    tmp_path, monkeypatch, stem_state
):
    import psycopg
    from botocore.exceptions import ClientError
    from stream_of_worship.admin.services import r2

    prefixes = ["first-song", "second-song", "third-song"]
    steps = [
        {"startSeconds": 1.5, "endSeconds": 2.0},
        {"startSeconds": 0.0, "endSeconds": 0.5},
        {"startSeconds": 1.5, "endSeconds": 2.0},
    ]
    items = [
        SongsetItem(i, prefix, prefix, prefix, 2.0, i == 2, 0.1, steps)
        for i, prefix in enumerate(prefixes)
    ]
    recordings = {
        prefix: Recording(prefix, None, 2, 120, "C", "major", None) for prefix in prefixes
    }
    source = np.repeat(np.array([0.125, 0.25, 0.375, 0.5], dtype=np.float32), SR // 2)
    source = np.stack((source, -source), axis=1)
    expected_guide = np.concatenate([source[-SR // 2 :], source[: SR // 2], source[-SR // 2 :]])
    requests = []
    downloads = []
    first_stem = "second-song/stems/instrumental.flac"
    uses_mix = stem_state in {"missing", "removed"}

    class FakeR2:
        def __init__(self, **_kwargs):
            pass

        def file_exists(self, key):
            if key == first_stem and stem_state == "denied":
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
            return not (key == first_stem and stem_state == "missing")

        def download_file(self, key, path):
            downloads.append(key)
            if key == first_stem and stem_state == "removed":
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            simdps.glue.write_flac(path, source, subtype="PCM_24")

    def remote(montage, guide, gap, seed, steps):
        if uses_mix and not requests:
            np.testing.assert_allclose(
                read_audio(BytesIO(guide)), expected_guide / 3, atol=1 / 32768
            )
        else:
            np.testing.assert_array_equal(read_audio(BytesIO(guide)), expected_guide)
        assert sf.info(BytesIO(guide)).format == "FLAC"
        assert sf.info(BytesIO(montage)).format == "FLAC"
        assert (gap, seed, steps) == ((SR, SR + SR // 2), 0, 100)
        np.testing.assert_array_equal(read_audio(BytesIO(montage))[slice(*gap)], 0)
        requests.append(guide)
        output = BytesIO()
        sf.write(output, np.full((SR // 2, 2), 0.2), SR, format="WAV", subtype="FLOAT")
        return output.getvalue(), {"sampling_seconds": 1.0}

    for name in simdps.ENV_KEYS:
        monkeypatch.setenv(name, "offline")
    monkeypatch.setattr(r2, "R2Client", FakeR2)
    monkeypatch.setattr(psycopg, "connect", lambda _: BytesIO())
    monkeypatch.setattr(simdps.glue, "fetch_songset", lambda *_: (items, recordings))
    monkeypatch.setattr(
        simdps.glue,
        "ensure_source_audio",
        lambda prefix, *_: source / (prefixes.index(prefix) + 2),
    )
    monkeypatch.setattr(
        simdps, "modal_inpainter", lambda: SimpleNamespace(inpaint=SimpleNamespace(remote=remote))
    )

    code = simdps.main(["--out-root", str(tmp_path), "--beats", "1", "--context-seconds", "1"])

    assert code == (2 if stem_state == "denied" else 0)
    assert len(requests) == (1 if stem_state == "denied" else 2)
    assert downloads[-1] == "third-song/stems/instrumental.flac"
    run = tmp_path / "run1"
    index = json.loads((run / "index.json").read_text())
    first, second = index["junctions"]
    assert first["baseline"]["mode"] == "gap"
    assert second["baseline"]["mode"] == "crossfade"
    assert all(
        (run / path).is_file()
        for entry in (first, second)
        for path in entry["baseline"]["files"].values()
    )
    take = second["takes"][0]
    assert take["status"] in {"ok", "check"}
    assert all((run / path).is_file() for path in take["files"].values())
    assert sf.info(run / take["files"]["generation"]).subtype == "FLOAT"
    a, b = [read_audio(run / index["items"][i]["file"]) for i in (1, 2)]
    pair = read_audio(run / take["files"]["full"])
    np.testing.assert_array_equal(pair[: len(a)], a)
    np.testing.assert_array_equal(pair[-len(b) :], b)
    assert len(pair) - len(a) - len(b) == SR // 2

    full_take = index["full_set"]["takes"][0]
    assert (run / index["full_set"]["baseline"]["files"]["full"]).is_file()
    report = (run / "report.html").read_text()
    assert "SimDPS Glue" in report
    assert "vocals heard" in report and "Export scores" in report
    assert take["files"]["full"] in report
    assert second["search_source"] == "instrumental_stem"
    assert (run / second["guide"]).is_file()
    if uses_mix:
        assert first["search_source"] == "incoming_mix"
        assert requests[0] == (run / first["to"]["file"]).read_bytes()
        assert "guide" not in first and "error" not in first
        assert "incoming mix search source 1.50 s · instrumental stem missing" in report
    elif stem_state == "present":
        assert first["search_source"] == "instrumental_stem"
    if stem_state == "denied":
        assert first["takes"][0]["status"] == "failed"
        assert "AccessDenied" in first["takes"][0]["error"]
        assert "AccessDenied" in report
        assert full_take["status"] == "unavailable"
        assert not (run / "full-set" / "beats-1.flac").exists()
    else:
        assert full_take["status"] == "ok"
        full = read_audio(run / full_take["files"]["full"])
        offset = 0
        for item in index["items"]:
            context = read_audio(run / item["file"])
            np.testing.assert_array_equal(full[offset : offset + len(context)], context)
            offset += len(context) + SR // 2
        assert len(full) == offset - SR // 2
