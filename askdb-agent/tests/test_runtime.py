from pathlib import Path

from askdb_agent.runtime import Settings, build_model


def test_build_model_uses_the_configured_deepseek_openai_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    settings = Settings(
        wren_project_dir=Path("/tmp/wren-project"),
        wren_profile="askdb_mysql",
        model="openai:deepseek-v4-flash",
    )

    model = build_model(settings)

    assert model.model_name == "deepseek-v4-flash"
    assert str(model.root_client.base_url).rstrip("/") == "https://api.deepseek.com"
