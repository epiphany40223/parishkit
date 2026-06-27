import pytest

from parishkit.config import ConfigError, load_yaml_config, require_keys, validate_with


def test_load_yaml_config_empty_when_optional_missing(tmp_path):
    assert load_yaml_config(tmp_path / "missing.yaml") == {}


def test_load_yaml_config_requires_mapping(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="must contain a mapping"):
        load_yaml_config(config_file)


def test_require_keys_reports_missing():
    with pytest.raises(ConfigError, match="alpha, beta"):
        require_keys({}, {"alpha", "beta"})


def test_validate_with_normalizes_value_errors():
    with pytest.raises(ConfigError, match="bad value"):
        validate_with(
            {},
            lambda _config: (_ for _ in ()).throw(ValueError("bad value")),
        )
