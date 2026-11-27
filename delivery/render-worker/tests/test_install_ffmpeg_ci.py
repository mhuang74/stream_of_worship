"""Tests for scripts/install-ffmpeg-ci.sh.

CI's FFmpeg install used to fail whole test runs when johnvansickle.com was
unreachable. The script now prefers this repo's release asset, falls back to
upstream, and refuses anything that fails the pinned checksum. These tests drive
the real script with a stubbed ``curl`` so source order and fail-fast behaviour
stay covered without network access.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
INSTALL_SCRIPT = SCRIPTS_DIR / "install-ffmpeg-ci.sh"

VERSION = "7.0.2"
ASSET = f"ffmpeg-{VERSION}-amd64-static.tar.xz"
RELEASE_URL = (
    "https://github.com/mhuang74/stream_of_worship/releases/download/ffmpeg-static/" + ASSET
)
UPSTREAM_URL = "https://johnvansickle.com/ffmpeg/releases/" + ASSET

FAKE_BINARY = """#!/bin/sh
case "$1" in
  -version) echo "ffmpeg version {version}-static https://johnvansickle.com/ffmpeg/";;
esac
exit 0
"""


def fake_archive(tmp_path: Path, version: str = VERSION) -> Path:
    """Build a minimal .tar.xz shaped like the real static build."""
    stage = tmp_path / f"stage-{version}"
    inner = stage / f"ffmpeg-{version}-amd64-static"
    inner.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        binary = inner / name
        binary.write_text(FAKE_BINARY.format(version=version))
        binary.chmod(0o755)

    archive = tmp_path / f"ffmpeg-{version}-amd64-static.tar.xz"
    if archive.exists():
        archive.unlink()
    subprocess.run(
        ["tar", "-cJf", str(archive), "-C", str(stage), f"ffmpeg-{version}-amd64-static"],
        check=True,
    )
    return archive


def stub_curl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapping: dict[str, Path]) -> Path:
    """Put a fake ``curl`` on PATH that serves ``mapping`` by URL and logs requests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "curl.log"

    mapping_path = tmp_path / "curl-mapping.txt"
    mapping_path.write_text("".join(f"{url}\t{path}\n" for url, path in mapping.items()))

    stub = bin_dir / "curl"
    stub.write_text(
        "#!/bin/sh\n"
        f'mapping="{mapping_path}"\n'
        f'log="{log}"\n'
        "out=\"\"\n"
        "prev=\"\"\n"
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "-o" ]; then out="$arg"; fi\n'
        "  prev=\"$arg\"\n"
        "done\n"
        'url=""\n'
        'for arg in "$@"; do url="$arg"; done\n'
        'printf "%s\\n" "$url" >> "$log"\n'
        'src=$(awk -v u="$url" \'BEGIN{FS="\\t"} $1==u {print $2}\' "$mapping")\n'
        'if [ -z "$src" ]; then exit 22; fi\n'
        'cp "$src" "$out"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def run_install(
    dest: Path, sha256: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT), VERSION, sha256, str(dest)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(dest.parent),
    )


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_prefers_the_release_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = fake_archive(tmp_path)
    log = stub_curl(tmp_path, monkeypatch, {RELEASE_URL: archive})
    dest = tmp_path / "dest"

    result = run_install(dest, sha256_of(archive))

    assert result.returncode == 0, result.stderr
    assert (dest / "ffmpeg").exists()
    assert os.access(dest / "ffmpeg", os.X_OK)
    assert log.read_text().split() == [RELEASE_URL]


def test_falls_back_to_the_upstream_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = fake_archive(tmp_path)
    log = stub_curl(tmp_path, monkeypatch, {UPSTREAM_URL: archive})
    dest = tmp_path / "dest"

    result = run_install(dest, sha256_of(archive))

    assert result.returncode == 0, result.stderr
    assert (dest / "ffmpeg").exists()
    assert os.access(dest / "ffmpeg", os.X_OK)
    assert log.read_text().split() == [RELEASE_URL, UPSTREAM_URL]


def test_checksum_mismatch_aborts_instead_of_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = fake_archive(tmp_path)
    stub_curl(tmp_path, monkeypatch, {RELEASE_URL: archive, UPSTREAM_URL: archive})
    dest = tmp_path / "dest"

    result = run_install(dest, "0" * 64)

    assert result.returncode != 0
    assert (
        f"Could not obtain an archive matching the pinned FFmpeg {VERSION} checksum"
        in result.stdout + result.stderr
    )
    assert not (dest / "ffmpeg").exists()
