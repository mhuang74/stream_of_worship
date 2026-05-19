import importlib
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("SKIP_DOCKER_TESTS") == "1", reason="Docker tests disabled")
class TestDockerBuild:
    IMAGE_NAME = "sow-render-worker-test"

    def test_docker_build_succeeds(self):
        result = subprocess.run(
            ["docker", "build", "-t", self.IMAGE_NAME, "."],
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
