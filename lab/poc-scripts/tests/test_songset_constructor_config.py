from poc.songset_constructor.config import RunConfig


def test_env_file_loads_llm_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SOW_LLM_MODEL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SOW_LLM_API_KEY=file-key\nSOW_LLM_MODEL=file-model\n",
        encoding="utf-8",
    )

    config = RunConfig(env_file=env_file)

    assert config.env_file == env_file
    assert config.llm_model == "file-model"
    assert config.validate_environment() is None


def test_env_file_does_not_override_exported_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_LLM_API_KEY", "exported-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "exported-model")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SOW_LLM_API_KEY=file-key\nSOW_LLM_MODEL=file-model\n",
        encoding="utf-8",
    )

    config = RunConfig(env_file=env_file)

    assert config.llm_model == "exported-model"
    assert config.validate_environment() is None


def test_no_llm_skips_llm_environment_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SOW_LLM_MODEL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = RunConfig(no_llm=True, env_file=env_file)

    assert config.validate_environment() is None


def test_config_closing_limit_respects_intimate_and_override():
    assert RunConfig(intimate=True).closing_limit == 80
    assert RunConfig(intimate=False).closing_limit == 90
    assert RunConfig(relax_h3_bpm=115).closing_limit == 115
    assert RunConfig(intimate=True, relax_h3_bpm=115).closing_limit == 115


def test_config_opening_floor_override():
    assert RunConfig().opening_floor == 110
    assert RunConfig(relax_h2_bpm=90).opening_floor == 90
    assert RunConfig(relax_h2_bpm=0).opening_floor == 0


def test_config_relax_bpm_negative_raises_value_error():
    import pytest

    with pytest.raises(ValueError):
        RunConfig(relax_h3_bpm=-1)
    with pytest.raises(ValueError):
        RunConfig(relax_h2_bpm=-1)


def test_to_dict_preserves_relax_fields():
    config = RunConfig(relax_h3_bpm=120, relax_h2_bpm=85, relax_h1=False, auto_relax=False)
    data = config.to_dict()
    assert data["relax_h3_bpm"] == 120
    assert data["relax_h2_bpm"] == 85
    assert data["relax_h1"] is False
    assert data["auto_relax"] is False

    child = RunConfig(**{**data, "songs": 4})
    assert child.relax_h3_bpm == 120
    assert child.relax_h2_bpm == 85
    assert child.relax_h1 is False
    assert child.auto_relax is False
    assert child.closing_limit == 120
    assert child.opening_floor == 85
