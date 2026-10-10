"""Tests for the modernized stem layer of AssetCache.

R2 now stores FLAC stems (vocals_dry.flac / vocals.flac / instrumental.flac,
plus legacy vocals_clean.flac and wet vocals.wav); the admin cache must fetch
those keys and produce the canonical ``stems/clean_vocals.flac`` slot that
downstream eval consumers read.
"""

from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.services.asset_cache import AssetCache


@pytest.fixture
def r2(tmp_path):
    client = MagicMock()
    client.file_exists.return_value = False

    # download_file writes the file (real R2Client also mkdirs parents) so
    # post-download existence checks pass
    def _download(s3_key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    client.download_file.side_effect = _download
    return client


@pytest.fixture
def cache(tmp_path, r2):
    return AssetCache(cache_dir=tmp_path, r2_client=r2)


def test_get_stem_path_defaults_to_flac(cache):
    path = cache.get_stem_path("abc123", "vocals_dry")
    assert path.name == "vocals_dry.flac"
    assert path.parent.name == "stems"


def test_download_stem_uses_flac_s3_key(cache, r2, tmp_path):
    hp = "abc123"
    r2.file_exists.return_value = True
    path = cache.download_stem(hp, "vocals_dry")
    assert path is not None
    assert path == tmp_path / hp / "stems" / "vocals_dry.flac"
    r2.file_exists.assert_called_once_with(f"{hp}/stems/vocals_dry.flac")
    assert path.read_bytes() == b"x"


def test_download_clean_vocals_prefers_dry(cache, r2, tmp_path):
    hp = "abc123"
    # Only vocals_dry exists in R2
    r2.file_exists.side_effect = lambda key: key == f"{hp}/stems/vocals_dry.flac"

    path, source = cache.download_clean_vocals(hp)

    alias = tmp_path / hp / "stems" / "clean_vocals.flac"
    assert path == alias
    assert alias.exists()
    assert alias.read_bytes() == path.read_bytes()
    assert source == "vocals_dry"


def test_download_clean_vocals_wet_not_aliased(cache, r2, tmp_path):
    hp = "abc123"
    r2.file_exists.side_effect = lambda key: key == f"{hp}/stems/vocals.wav"

    path, source = cache.download_clean_vocals(hp)

    assert source == "vocals_wav"
    assert path == tmp_path / hp / "stems" / "vocals.wav"
    assert not (tmp_path / hp / "stems" / "clean_vocals.flac").exists()


def test_download_clean_vocals_none_available(cache, r2):
    hp = "abc123"
    path, source = cache.download_clean_vocals(hp)
    assert path is None
    assert source is None


def test_download_clean_vocals_short_circuits_existing_alias(cache, r2, tmp_path):
    hp = "abc123"
    alias = tmp_path / hp / "stems" / "clean_vocals.flac"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_bytes(b"cached")

    path, source = cache.download_clean_vocals(hp)

    assert source == "clean_vocals"
    assert path == alias
    r2.file_exists.assert_not_called()
