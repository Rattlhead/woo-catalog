from woo_catalog.config import WooConfig
from woo_catalog.config import ConfigurationError
import pytest


def test_separate_env_files_do_not_leak_values_between_loads(tmp_path, monkeypatch):
    for name in (
        "WOO_BASE_URL",
        "WOO_CONSUMER_KEY",
        "WOO_CONSUMER_SECRET",
        "WOO_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text(
        "WOO_BASE_URL=https://first.example\nWOO_CONSUMER_KEY=ck_first\nWOO_CONSUMER_SECRET=cs_first\n",
        encoding="utf-8",
    )
    second.write_text(
        "WOO_BASE_URL=https://second.example\nWOO_CONSUMER_KEY=ck_second\nWOO_CONSUMER_SECRET=cs_second\n",
        encoding="utf-8",
    )

    first_config = WooConfig.from_env(first)
    second_config = WooConfig.from_env(second)

    assert first_config.store_identity == "https://first.example"
    assert second_config.store_identity == "https://second.example"


def test_read_retry_settings_have_safe_defaults_and_bounded_validation():
    config = WooConfig(
        base_url="https://shop.example",
        consumer_key="ck_test",
        consumer_secret="cs_test",
    )

    assert config.read_retry_attempts == 3
    assert config.read_retry_backoff_seconds == 0.5
    with pytest.raises(ConfigurationError, match="RETRY_ATTEMPTS"):
        WooConfig("https://shop.example", "ck", "cs", read_retry_attempts=6)
