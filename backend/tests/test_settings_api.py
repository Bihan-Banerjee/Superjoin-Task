"""Configuration over HTTP.

This endpoint writes a file and holds API keys, so most of what is worth testing is what it
refuses. The rest checks that a hand-written `.env` survives being edited by a machine.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.config import get_settings, reset_settings_cache


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A throwaway `.env`, with a comment and an unrelated setting to preserve."""
    path = tmp_path / ".env"
    path.write_text(
        "# hand written, keep me\n"
        "GEMINI_API_KEY=old-key-1234\n"
        "LLM_RATE_LIMIT_RPM=15\n"
        "\n"
        "# something this module knows nothing about\n"
        "SOME_OTHER_SETTING=leave-me-alone\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_api, "ENV_PATH", path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("READ_ONLY", "false")

    # conftest detaches the env file for the whole session so tests never read the
    # developer's own. Here the round trip *is* the thing under test, so this one points at
    # the throwaway file instead of at nothing.
    from app.config import Settings

    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = path
    reset_settings_cache()
    yield path
    Settings.model_config["env_file"] = original
    reset_settings_cache()


def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_a_key_is_never_sent_back(env, monkeypatch):
    """Enough to tell two keys apart, not enough to use one."""
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-abcd")
    reset_settings_cache()

    with client() as api:
        body = api.get("/api/settings").json()

    gemini = body["values"]["GEMINI_API_KEY"]
    assert gemini == {"configured": True, "hint": "abcd"}
    assert "secret-value-abcd" not in api_text(body)


def api_text(body: object) -> str:
    import json

    return json.dumps(body)


def test_writing_preserves_comments_and_unknown_settings(env):
    with client() as api:
        response = api.patch("/api/settings", json={"LLM_RATE_LIMIT_RPM": 5})
    assert response.status_code == 200

    written = env.read_text(encoding="utf-8")
    assert "# hand written, keep me" in written
    assert "SOME_OTHER_SETTING=leave-me-alone" in written
    assert "LLM_RATE_LIMIT_RPM=5" in written
    # The old value is gone rather than duplicated.
    assert written.count("LLM_RATE_LIMIT_RPM=") == 1


def test_a_setting_not_already_present_is_appended(env):
    with client() as api:
        api.patch("/api/settings", json={"ENABLE_GRAPH_VIEW": True})
    assert "ENABLE_GRAPH_VIEW=true" in env.read_text(encoding="utf-8")


def test_a_change_takes_effect_immediately(env):
    with client() as api:
        api.patch("/api/settings", json={"ENABLE_GRAPH_VIEW": True})
        assert api.get("/api/health").json()["graph_view_enabled"] is True


def test_only_listed_settings_can_be_written(env):
    with client() as api:
        response = api.patch("/api/settings", json={"DATABASE_URL": "sqlite:///elsewhere.db"})
    assert response.status_code == 400
    assert "not editable" in response.json()["detail"]
    assert "elsewhere" not in env.read_text(encoding="utf-8")


def test_a_choice_outside_the_list_is_refused(env):
    with client() as api:
        response = api.patch("/api/settings", json={"LLM_PROVIDER": "something-invented"})
    assert response.status_code == 400
    assert "something-invented" not in env.read_text(encoding="utf-8")


def test_a_number_field_refuses_prose(env):
    with client() as api:
        assert api.patch("/api/settings", json={"LLM_CONCURRENCY": "lots"}).status_code == 400


def test_read_only_refuses_configuration_entirely(env, monkeypatch):
    """A deployment serving a snapshot has no business holding a key."""
    monkeypatch.setenv("READ_ONLY", "true")
    reset_settings_cache()

    with client() as api:
        assert api.get("/api/settings").status_code == 403
        assert api.patch("/api/settings", json={"ENABLE_GRAPH_VIEW": True}).status_code == 403


def test_clearing_a_secret_is_allowed(env):
    with client() as api:
        api.patch("/api/settings", json={"GEMINI_API_KEY": ""})
    assert "GEMINI_API_KEY=\n" in env.read_text(encoding="utf-8")


def test_a_pasted_key_is_stripped(env):
    with client() as api:
        api.patch("/api/settings", json={"GEMINI_API_KEY": "  spaced-key-wxyz \n"})
    assert "GEMINI_API_KEY=spaced-key-wxyz" in env.read_text(encoding="utf-8")


def test_remote_callers_are_refused_by_default(env, monkeypatch):
    """The rest of the API is safe to expose to a reader; this part is not."""
    reset_settings_cache()
    request = _request_from("203.0.113.9")
    with pytest.raises(Exception) as raised:
        settings_api._guard(request)
    assert "only be changed from the machine" in str(raised.value.detail)

    monkeypatch.setenv("ALLOW_REMOTE_CONFIG", "true")
    reset_settings_cache()
    assert settings_api._guard(request) is get_settings()


def _request_from(host: str):
    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host

    class _Request:
        def __init__(self, host: str) -> None:
            self.client = _Client(host)

    return _Request(host)


def test_a_failed_write_leaves_the_original_intact(env, monkeypatch):
    """The write goes to a sibling and is moved into place, so a crash cannot truncate `.env`.

    Also pins the temporary name. `.env` has no suffix as Path sees it, so the previous
    `with_suffix(".env.tmp")` produced `.env.env.tmp`: harmless on the happy path, because
    the move deletes it, and a confusing file to find in the repo after a crash.
    """
    from pathlib import Path

    seen: list[str] = []

    def explode(self, target):
        seen.append(self.name)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", explode)
    before = env.read_text(encoding="utf-8")

    with client() as api, pytest.raises(OSError):
        api.patch("/api/settings", json={"LLM_CONCURRENCY": 4})

    assert seen == [".env.tmp"], seen
    assert env.read_text(encoding="utf-8") == before


def test_clearing_a_number_field_leaves_the_setting_alone(env):
    """An emptied input means "do not change this", not "set it to nothing"."""
    with client() as api:
        response = api.patch("/api/settings", json={"LLM_RATE_LIMIT_RPM": ""})

    assert response.status_code == 200
    assert "LLM_RATE_LIMIT_RPM=15" in env.read_text(encoding="utf-8")


def test_a_number_field_still_refuses_nonsense(env):
    with client() as api:
        assert api.patch("/api/settings", json={"LLM_RATE_LIMIT_RPM": "many"}).status_code == 400
