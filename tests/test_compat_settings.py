import pytest

from woo_catalog.settings import SettingsSynchronizer, export_settings, validate_settings_document


class FakeWooClient:
    def __init__(self): self.puts = []
    def list_all(self, path, params=None):
        return {"settings": [{"id": "general"}, {"id": "products"}], "settings/general": [{"id": "woocommerce_currency", "value": "RUB", "type": "select"}], "settings/products": [{"id": "woocommerce_enable_reviews", "value": "yes"}]}[path]
    def put(self, path, payload): self.puts.append((path, payload)); return payload


def test_export_settings_preserves_all_groups_and_api_values():
    assert export_settings(FakeWooClient()) == {"schema_version": 1, "settings": [{"group_id": "general", "settings": [{"id": "woocommerce_currency", "value": "RUB"}]}, {"group_id": "products", "settings": [{"id": "woocommerce_enable_reviews", "value": "yes"}]}]}


def test_settings_import_updates_only_changed_values_when_applied():
    groups = validate_settings_document({"schema_version": 1, "settings": [{"group_id": "general", "settings": [{"id": "woocommerce_currency", "value": "USD"}]}]})
    client = FakeWooClient(); SettingsSynchronizer(client).run(groups, apply=True)
    assert client.puts == [("settings/general/woocommerce_currency", {"value": "USD"})]


def test_settings_document_rejects_export_only_fields_and_unknown_structure():
    with pytest.raises(ValueError, match="only id and value"):
        validate_settings_document({"schema_version": 1, "settings": [{"group_id": "general", "settings": [{"id": "woocommerce_currency", "value": "USD", "type": "text"}]}]})
