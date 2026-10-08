from woo_catalog.schemas import SettingsChange
from woo_catalog.settings import SettingsPlanner


class SettingsClient:
    def list_all(self, path, params=None):
        return {"settings/general": [{"id": "woocommerce_currency", "value": "RUB", "type": "select"}]}[path]


def test_settings_plan_contains_diff_and_requires_specific_confirmation():
    change = SettingsChange.model_validate({"schema_version": 1, "settings": [{"group_id": "general", "settings": [{"id": "woocommerce_currency", "value": "USD"}]}]})
    plan = SettingsPlanner(SettingsClient(), store_identity="https://shop.example").plan(change)
    operation = plan.operations[0]
    assert operation.before == {"value": "RUB"}
    assert operation.after == {"value": "USD"}
    assert operation.changes == {"value": {"before": "RUB", "after": "USD"}}
    assert plan.required_confirmations == {"confirmed", "confirm_settings"}
