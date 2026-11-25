"""Offline tests of the SimDPS Glue CLI and the shared crop contract."""

import json
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import gen_glue_simdps as simdps
from gen_glue_elevenlabs import SR, Recording, SongsetItem
from sow_render_worker.simdps_audio import extract_fill


@pytest.mark.parametrize("provider,expected", [("modal", "122,880"), ("runpod", "132,300")])
def test_dry_run_has_no_io(tmp_path, monkeypatch, capsys, provider, expected):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run tried to load environment or connect to a service")

    monkeypatch.setattr(simdps, "execute_run", forbidden)
    monkeypatch.setattr(simdps.glue, "load_env_file", forbidden)
    monkeypatch.setattr(simdps, "modal_inpainter", forbidden)
    args = ["--dry-run", "--dry-run-items", "5", "--out-root", str(tmp_path),
            "--provider", provider]
    if provider == "runpod":
        args.append("--production-duration")
    assert simdps.main(args) == 0
    output = capsys.readouterr().out
    assert f"{expected} frames" in output
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
        return output.getvalue(), {"sampling_seconds": 1.0, "gpu": "NVIDIA L4"}

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
    assert (index["provider"], index["duration_mode"]) == ("modal", "beat-exact")
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
    assert take["gpu_check"] == "pass"
    assert take["guide_frames"] == 3 * SR // 2
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


@pytest.fixture
def runpod_env(tmp_path, monkeypatch):
    env_file = tmp_path / "fixture.env"
    keys = simdps.ENV_KEYS + simdps.RUNPOD_ENV_KEYS
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    values = dict.fromkeys(keys, "offline")
    values.update(SOW_R2_BUCKET="catalog", SIMDPS_POC_R2_BUCKET="scratch")
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    load = simdps.glue.load_env_file
    monkeypatch.setattr(simdps.glue, "load_env_file", lambda **kwargs: load(env_file, **kwargs))
    return env_file


@pytest.mark.parametrize(
    "settings, error",
    [
        (dict(RUNPOD_ENDPOINT_ID="https://private/signed"), "RUNPOD_ENDPOINT_ID"),
        (dict(RUNPOD_API_KEY=" "), "RUNPOD_API_KEY"),
        (dict(SIMDPS_POC_R2_BUCKET="catalog"), "must differ"),
        (dict(SIMDPS_POC_R2_BUCKET=" "), "valid R2 bucket name"),
    ],
)
def test_runpod_config_rejected_before_artifacts_or_catalog(
    tmp_path, monkeypatch, runpod_env, settings, error,
):
    import psycopg

    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration reached the catalog")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=error) as exc:
        simdps.main(["--provider", "runpod", "--out-root", str(tmp_path / "artifacts")])
    assert "https://" not in str(exc.value)
    assert not (tmp_path / "artifacts").exists()


def test_runpod_requires_config_keys(tmp_path, runpod_env):
    runpod_env.write_text("")
    with pytest.raises(SystemExit) as exc:
        simdps.main(["--provider", "runpod", "--out-root", str(tmp_path / "artifacts")])
    assert all(key in str(exc.value) for key in simdps.RUNPOD_ENV_KEYS)
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    "stem_seconds,song_seconds,gpu,source",
    [
        (22, 22, "NVIDIA L4", "instrumental_stem"),
        (None, 22, None, "incoming_mix"),
        (2, 22, "NVIDIA A100", "incoming_mix"),
        (1, 2, None, None),
    ],
)
def test_runpod_artifacts_and_preflight(
    tmp_path, monkeypatch, runpod_env, stem_seconds, song_seconds, gpu, source,
):
    import psycopg
    from stream_of_worship.admin.services import r2

    def stereo(seconds, value):
        return np.full((seconds * SR, 2), value, dtype=np.float32)

    items = [
        SongsetItem(i, prefix, prefix, prefix, 2, False, None,
                    [{"startSeconds": 0, "endSeconds": song_seconds}])
        for i, prefix in enumerate(("outgoing", "incoming"))
    ]
    recordings = {
        item.hash_prefix: Recording(item.hash_prefix, None, song_seconds, 86.13281,
                                    "C", "major", None)
        for item in items
    }
    songs = {"outgoing": stereo(song_seconds, 0.125), "incoming": stereo(song_seconds, 0.25)}
    # Mark the region beyond the cap so choosing the tail would be detected.
    songs["incoming"][18 * SR :] *= -1
    stem = stereo(stem_seconds, 0.375) if stem_seconds is not None else None
    if stem is not None:
        stem[18 * SR :] *= -1

    class FakeR2:
        def __init__(self, *, bucket, endpoint_url):
            assert bucket == "catalog"

        def file_exists(self, key):
            return stem is not None

        def download_file(self, key, path):
            simdps.glue.write_flac(path, stem, subtype="PCM_24")

    def no_modal():
        pytest.fail("RunPod loaded the Modal client")

    monkeypatch.setattr(r2, "R2Client", FakeR2)
    monkeypatch.setattr(psycopg, "connect", lambda _: BytesIO())
    monkeypatch.setattr(simdps.glue, "fetch_songset", lambda *_: (items, recordings))
    monkeypatch.setattr(simdps.glue, "ensure_source_audio", lambda prefix, *_: songs[prefix])
    monkeypatch.setattr(simdps, "modal_inpainter", no_modal)
    launches = []

    def subprocess_run(command, *, env, **kwargs):
        prefix, fixture, montage, guide, output, a, end, seed, beats, bpm, search = (
            command[:5], *command[5:]
        )
        assert prefix[:3] == ["uv", "run", "--project"]
        assert Path(fixture).name == "runpod_glue_fixture.py"
        assert env["SOW_R2_BUCKET"] == "catalog" and env["SIMDPS_POC_R2_BUCKET"] == "scratch"
        assert env["RUNPOD_ENDPOINT_ID"] == os.environ["RUNPOD_ENDPOINT_ID"] == "offline"
        assert (seed, beats, bpm, search) == ("0", "4", "86.13281", source)
        start, end = int(a), int(end)
        assert end - start == 132300
        np.testing.assert_array_equal(read_audio(montage)[start:end], 0)
        np.testing.assert_array_equal(
            read_audio(guide), (stem if source == "instrumental_stem" else songs["incoming"])[:18 * SR],
        )
        sf.write(output, stereo(3, 0.0625), SR, subtype="FLOAT")
        sf.write(str(Path(output).with_suffix(".staged.wav")), stereo(3, 0.0625), SR)
        meta = {"sampling_seconds": 11, "model_load_seconds": 2, "processing_seconds": 12}
        if gpu:
            meta["gpu"] = gpu
        launches.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(meta))

    original = subprocess.run

    def run(command, *args, **kwargs):
        if command[0] == "uv":
            return subprocess_run(command, **kwargs)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert simdps.main([
        "--provider", "runpod", "--production-duration", "--beats", "40,4",
        "--out-root", str(tmp_path),
    ]) == 2
    run_dir = tmp_path / "run1"
    index = json.loads((run_dir / "index.json").read_text())
    assert (index["provider"], index["duration_mode"]) == ("runpod", "production")
    invalid, take = index["junctions"][0]["takes"]
    assert invalid["status"] == "failed" and "supported SimDPS window" in invalid["error"]
    assert invalid["files"] == {}
    if source is None:
        assert not launches
        assert take["status"] == "failed" and "Neither the instrumental stem" in take["error"]
        return
    assert len(launches) == 1
    assert take["status"] in {"ok", "check"}
    assert (take["search_source"], take["guide_frames"]) == (source, 18 * SR)
    assert take["gpu_check"] == {None: "unverified", "NVIDIA L4": "pass", "NVIDIA A100": "fail"}[gpu]
    for kind in ("generation", "productionStaged"):
        assert read_audio(run_dir / take["files"][kind]).shape == (132300, 2)
    for kind in ("full", "productionFull"):
        pair = read_audio(run_dir / take["files"][kind])
        a, b = songs.values()
        np.testing.assert_array_equal(pair[:len(a)], a)
        np.testing.assert_array_equal(pair[-len(b):], b)
        assert len(pair) == len(a) + 132300 + len(b)
    report = (run_dir / "report.html").read_text()
    assert "RunPod · production duration" in report
    assert (gpu or "GPU unverified") in report
    assert "11.00 s sampling" in report and "2.00 s model load" in report
    assert set(take["files"].values()) <= set(re.findall(r'<audio [^>]* src="([^"]+)"', report))
    for path in re.findall(r'<audio [^>]* src="([^"]+)"', report):
        assert sf.info(run_dir / path).samplerate == SR


@pytest.mark.parametrize("returncode, msg", [(21, "write access"), (22, "rejected"), (1, "environment")])
def test_runpod_fixture_errors_are_sanitized(tmp_path, monkeypatch, returncode, msg):
    secret = "api-key https://signed-url.invalid/?X-Amz-Signature=secret"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout=secret, stderr=secret,
    ))
    with pytest.raises(RuntimeError, match=msg) as exc:
        simdps.runpod_inpaint(
            tmp_path / "montage.flac", np.zeros((10, 2)), 1, 2, 0, 4, 86, "incoming_mix"
        )
    assert "api-key" not in str(exc.value) and "https://" not in str(exc.value)
