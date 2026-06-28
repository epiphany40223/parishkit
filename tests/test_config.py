import pytest

from parishkit.config import ConfigError, load_yaml_config, require_keys, validate_with


def test_load_yaml_config_empty_when_optional_missing(tmp_path):
    """A missing optional config file loads as an empty dict instead of raising."""
    assert load_yaml_config(tmp_path / "missing.yaml") == {}


def test_load_yaml_config_requires_mapping(tmp_path):
    """A YAML file whose top level is not a mapping raises ConfigError."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="must contain a top-level mapping"):
        load_yaml_config(config_file)


def test_load_yaml_config_parse_error_includes_repair_hint(tmp_path):
    """Invalid YAML reports file location and common syntax repair hints."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("common:\n  dry_run: true\n  bad: [\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_yaml_config(config_file)

    message = str(exc_info.value)
    assert f"could not parse YAML config file {config_file}" in message
    assert "line" in message
    assert "Check indentation" in message


def test_require_keys_reports_missing():
    """Missing required keys are all named in the raised ConfigError message."""
    with pytest.raises(ConfigError, match="alpha, beta"):
        require_keys({}, {"alpha", "beta"})


def test_validate_with_normalizes_value_errors():
    """A ValueError from a validator is re-raised as a ConfigError, message intact."""
    with pytest.raises(ConfigError, match="bad value"):
        validate_with(
            {},
            lambda _config: (_ for _ in ()).throw(ValueError("bad value")),
        )
