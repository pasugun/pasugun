from engine.config import Settings, TossMode


def test_toss_mode_defaults_to_mock(monkeypatch) -> None:
    monkeypatch.delenv("TOSS_MODE", raising=False)

    assert Settings(_env_file=None).toss_mode is TossMode.MOCK


def test_secrets_are_not_exposed_in_repr(monkeypatch) -> None:
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "super-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")

    settings = Settings(_env_file=None)

    assert "super-secret" not in repr(settings)
    assert "bot-token" not in repr(settings)
    assert settings.toss_client_secret is not None
    assert settings.toss_client_secret.get_secret_value() == "super-secret"
