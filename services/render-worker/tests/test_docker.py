import importlib
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("SKIP_DOCKER_TESTS") == "1", reason="Docker tests disabled")
class TestDockerBuild:
    IMAGE_NAME = "sow-render-worker-test"

    def test_docker_build_succeeds(self):
        build_cmd = [
            "docker",
            "build",
            "-t",
            self.IMAGE_NAME,
        ]
        for arg_name in ("R2_BUCKET", "R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
            env_val = os.environ.get(f"SOW_{arg_name}")
            if env_val:
                build_cmd.extend(["--build-arg", f"{arg_name}={env_val}"])
        build_cmd.append(".")
        result = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0, f"docker build failed:\n{result.stderr}"

    def test_handler_importable_in_container(self):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "",
                self.IMAGE_NAME,
                "python",
                "-c",
                "from sow_render_worker.lambda_handler import handler; print('OK')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"handler import failed:\n{result.stderr}"
        assert "OK" in result.stdout

    def test_ffmpeg_available_in_container(self):
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME, "ffmpeg", "-version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ffmpeg not found:\n{result.stderr}"

    # Mirrors Phase 1 feature checklist in specs/vendor-ffmpeg-via-r2-v2.md.
    # Keep these assertions in sync if the spec's required encoder/filter list grows.
    def test_ffprobe_available_in_container(self):
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
             "ffprobe", "-version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"ffprobe not found:\n{result.stderr}"
        assert "ffprobe version" in result.stderr or "ffprobe version" in result.stdout

    def test_ffmpeg_encoders_available(self):
        # Required for the project's audio/video pipeline
        expected_encoders = ("libx264", "libmp3lame", "aac")
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
             "ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"ffmpeg -encoders failed:\n{result.stderr}"
        for enc in expected_encoders:
            assert enc in result.stdout, f"missing encoder {enc!r} in ffmpeg -encoders output"

    def test_ffmpeg_filters_available(self):
        # Used by audio_engine.py (amix, afade, adelay, loudnorm, asetpts) and
        # video_engine.py (color). Listed in spec Phase 1 feature checklist.
        expected_filters = ("loudnorm", "amix", "afade", "adelay", "asetpts", "color")
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
             "ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"ffmpeg -filters failed:\n{result.stderr}"
        for flt in expected_filters:
            assert flt in result.stdout, f"missing filter {flt!r} in ffmpeg -filters output"

    def test_cjk_fonts_available_in_container(self):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "",
                self.IMAGE_NAME,
                "python",
                "-c",
                "from PIL import ImageFont; f = ImageFont.truetype('/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc', 24); print('OK')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"CJK font not found:\n{result.stderr}"

    def test_docker_compose_config_valid(self):
        compose_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["docker", "compose", "config", "--quiet"],
            capture_output=True,
            text=True,
            cwd=compose_dir,
        )
        assert result.returncode == 0, f"docker compose config invalid:\n{result.stderr}"


class TestHandlerImportable:
    def test_handler_module_importable(self):
        mod = importlib.import_module("sow_render_worker.lambda_handler")
        assert hasattr(mod, "handler")
        assert callable(mod.handler)

    def test_config_module_importable(self):
        mod = importlib.import_module("sow_render_worker.config")
        assert hasattr(mod, "RenderWorkerConfig")
        assert hasattr(mod, "load_config")

    def test_pipeline_module_importable(self):
        mod = importlib.import_module("sow_render_worker.pipeline")
        assert hasattr(mod, "execute_render_pipeline")
