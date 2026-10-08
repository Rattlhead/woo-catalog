import pytest

from woo_catalog.planners import CatalogPlanner
from woo_catalog.planning import PlanLimitError, canonical_document, stable_hash
from woo_catalog.schemas import CatalogChange


def test_canonical_change_preserves_explicit_null_without_defaults():
    raw = {
        "schema_version": 1,
        "categories": [{"name": "Child", "slug": "child", "parent_slug": None}],
    }

    change = CatalogChange.model_validate(raw)

    assert canonical_document(change) == raw


def test_explicit_null_and_absent_field_have_different_hashes():
    explicit = CatalogChange.model_validate(
        {
            "schema_version": 1,
            "categories": [{"name": "Child", "slug": "child", "parent_slug": None}],
        }
    )
    absent = CatalogChange.model_validate(
        {"schema_version": 1, "categories": [{"name": "Child", "slug": "child"}]}
    )

    assert stable_hash(explicit) != stable_hash(absent)


def test_canonical_change_does_not_add_attribute_defaults():
    raw = {
        "schema_version": 1,
        "attributes": [{"name": "Color", "slug": "pa_color"}],
    }

    assert canonical_document(CatalogChange.model_validate(raw)) == raw


def test_explicit_entity_limit_is_rejected_before_network_reads():
    class NoNetworkClient:
        def list_all(self, path, params=None):
            raise AssertionError("network must not be read for an oversized input")

    change = CatalogChange.model_validate(
        {
            "schema_version": 1,
            "categories": [
                {"name": f"Category {index}", "slug": f"category-{index}"}
                for index in range(101)
            ],
        }
    )

    with pytest.raises(PlanLimitError, match="101 explicit categories"):
        CatalogPlanner(NoNetworkClient()).plan(change)
