import os

import pytest
import yaml

from tplink_deco_exporter.config import load_config


@pytest.fixture(autouse=True)
def clean_deco_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("DECO_") or key == "CONFIG_PATH":
            monkeypatch.delenv(key, raising=False)


def test_yaml_secret_file_and_defaults(tmp_path):
    secret = tmp_path / "password"
    secret.write_text("super-private-value\n")
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump({"api": {"host": "http://deco", "password_file": str(secret)}})
    )
    config = load_config(path)
    assert config.api.resolved_password() == "super-private-value"
    assert config.logs.state_path.as_posix() == "/data/log-watermark.json"
    assert config.logs.level == "INFO"
    assert "super-private-value" not in repr(config)


def test_environment_overrides_nested_values(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"
    path.write_text("api:\n  host: http://old\n  password: old-secret\n")
    monkeypatch.setenv("DECO_API_HOST", "http://new")
    monkeypatch.setenv("DECO_API_PASSWORD", "new-secret")
    monkeypatch.setenv("DECO_LOGS_ENABLED", "true")
    monkeypatch.setenv("DECO_LOKI_URL", "http://loki/push")
    monkeypatch.setenv("DECO_INSTANCE", "mesh-a")
    config = load_config(path)
    assert config.api.host == "http://new"
    assert config.api.resolved_password() == "new-secret"
    assert config.logs.enabled and config.instance == "mesh-a"


def test_logs_require_loki_and_invalid_level_is_rejected(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "api:\n  host: http://deco\n  password: secret\nlogs:\n  enabled: true\n"
    )
    with pytest.raises(ValueError, match="loki.url"):
        load_config(path)
    path.write_text(
        "api:\n  host: http://deco\n  password: secret\nlogs:\n  level: VERBOSE\n"
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_extra_fields_and_secret_error_are_safe(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "api:\n  host: http://deco\n  password: extremely-secret\n  control_router: true\n"
    )
    with pytest.raises(ValueError) as error:
        load_config(path)
    assert "extremely-secret" not in str(error.value)
    assert "control_router" in str(error.value)
