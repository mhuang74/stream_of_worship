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
