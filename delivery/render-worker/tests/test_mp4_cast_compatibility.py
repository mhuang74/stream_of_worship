from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sow_render_worker.r2_client import FILE_TYPE_CONFIGS
from sow_render_worker.uploader import R2Uploader, infer_content_type
from sow_render_worker.video_engine import VideoEngine


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def _atom_order(mp4_path: Path) -> list[str]:
    order: list[str] = []
    data = mp4_path.read_bytes()
    offset = 0
    n = len(data)
    while offset + 8 <= n:
        size = int.from_bytes(data[offset : offset + 4], "big")
        atom_type = data[offset + 4 : offset + 8].decode("ascii", errors="replace")
        if size == 0:
            order.append(atom_type)
            break
        if size < 8:
            order.append(atom_type)
            break
        order.append(atom_type)
        offset += size
    return order


class _MockAssetFetcher:
    def download_lrc(self, hash_prefix: str) -> str | None:
        return None

    def get_temp_dir(self) -> Path:
        return Path("/tmp")


def _make_silent_audio(path: Path, duration_seconds: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=stereo:d={duration_seconds}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


@pytest.mark.skipif(not _ffprobe_available(), reason="ffmpeg/ffprobe not available")
class TestMp4CastCompatibility:
    @pytest.fixture(scope="class")
    def sample_render_output(self, tmp_path_factory) -> Path:
        tmp = tmp_path_factory.mktemp("mp4compat")
        audio_path = tmp / "audio.mp3"
        _make_silent_audio(audio_path, duration_seconds=2.0)

        output_path = tmp / "output.mp4"
        fetcher = _MockAssetFetcher()
        engine = VideoEngine(fetcher, ffmpeg_path=shutil.which("ffmpeg"))
        engine.generate_blank_video(str(audio_path), str(output_path), 2.0, job_id="compat-test")
        assert output_path.exists() and output_path.stat().st_size > 0
        return output_path

    def _ffprobe_streams(self, mp4_path: Path) -> dict:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_video_codec_is_h264(self, sample_render_output: Path):
        data = self._ffprobe_streams(sample_render_output)
        video_streams = [s for s in data["streams"] if s["codec_type"] == "video"]
        assert len(video_streams) == 1
        assert video_streams[0]["codec_name"] == "h264"

    def test_video_profile_is_android_compatible_yuv420p(self, sample_render_output: Path):
        data = self._ffprobe_streams(sample_render_output)
        video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        assert video_stream["profile"] != "High 4:4:4 Predictive"
        assert video_stream["pix_fmt"] == "yuv420p"

    def test_audio_codec_is_aac(self, sample_render_output: Path):
        data = self._ffprobe_streams(sample_render_output)
        audio_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) == 1
        assert audio_streams[0]["codec_name"] == "aac"

    def test_moov_atom_precedes_mdat(self, sample_render_output: Path):
        order = _atom_order(sample_render_output)
        assert "moov" in order, "no moov atom found in sample render output"
        assert "mdat" in order, "no mdat atom found in sample render output"
        assert order.index("moov") < order.index("mdat"), (
            f"faststart violated: moov must precede mdat, got atom order {order}"
        )

    def test_upload_content_type_remains_video_mp4(self, sample_render_output: Path):
        assert infer_content_type("renders/test-job/output.mp4") == "video/mp4"
        assert FILE_TYPE_CONFIGS["video"]["content_type"] == "video/mp4"

        upload_key = "renders/compat-test/output.mp4"
        mock_client = MagicMock()
        uploader = R2Uploader.__new__(R2Uploader)
        uploader._client = mock_client
        uploader._bucket_name = "test-bucket"

        from sow_render_worker.uploader import RenderArtifacts

        result = uploader.upload_render_artifacts(
            "compat-test",
            RenderArtifacts(mp4_path=str(sample_render_output)),
        )

        assert result.mp4_r2_key == upload_key
        upload_calls = mock_client.upload_file.call_args_list
        assert len(upload_calls) == 1
        extra_args = upload_calls[0].kwargs["ExtraArgs"]
        assert extra_args["ContentType"] == "video/mp4"
