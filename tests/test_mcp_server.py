import asyncio
import json

from woo_catalog.mcp_server import create_server
from woo_catalog.service import CatalogService


def test_mcp_server_advertises_narrow_tools_with_safety_annotations():
    server = create_server(CatalogService(client_factory=lambda _: None))

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools) == {
        "catalog_validate",
        "catalog_export",
        "catalog_get_product",
        "catalog_list_products",
        "catalog_search_taxonomy",
        "catalog_schema",
        "catalog_diagnose",
        "operation_status",
        "settings_export",
        "catalog_plan_sync",
        "catalog_apply_sync",
        "settings_plan_update",
        "settings_apply_update",
        "catalog_plan_delete",
        "catalog_apply_delete",
    }
    assert tools["catalog_plan_sync"].annotations.readOnlyHint is True
    assert tools["catalog_apply_sync"].annotations.readOnlyHint is False
    assert tools["catalog_apply_delete"].annotations.destructiveHint is True
    assert "plan" in (tools["catalog_apply_sync"].description or "").lower()


def test_mcp_server_exposes_workflow_resources_and_safe_prompt():
    server = create_server(CatalogService(client_factory=lambda _: None))

    resources = {str(item.uri) for item in asyncio.run(server.list_resources())}
    prompts = {item.name for item in asyncio.run(server.list_prompts())}

    assert {"woo-catalog://workflow", "woo-catalog://store-capabilities"} <= resources
    assert "safe_catalog_sync" in prompts


def test_mcp_server_returns_structured_validation_result():
    server = create_server(CatalogService(client_factory=lambda _: None))

    _, structured = asyncio.run(server.call_tool("catalog_validate", {"document": {"schema_version": 1}}))

    assert structured["document"]["schema_version"] == 1
    assert structured["report"]["mode"] == "validate"


def test_plan_tool_exposes_nested_catalog_schema():
    server = create_server(CatalogService(client_factory=lambda _: None))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    schema = tools["catalog_plan_sync"].inputSchema["properties"]["document"]
    serialized = json.dumps(schema)

    assert "schema_version" in serialized
    assert "products" in serialized
    assert "regular_price" in serialized
    assert schema.get("additionalProperties") is not True


def test_validation_error_has_stable_code_path_and_hint():
    server = create_server(CatalogService(client_factory=lambda _: None))

    _, structured = asyncio.run(
        server.call_tool(
            "catalog_validate",
            {"document": {"schema_version": 1, "products": [{"sku": "A", "regular_price": "bad"}]}},
        )
    )

    assert structured["error"]["code"] == "validation_error"
    assert structured["error"]["path"] == "products.0.regular_price"
    assert structured["error"]["hint"]


def test_server_instructions_treat_store_content_as_untrusted_data():
    server = create_server(CatalogService(client_factory=lambda _: None))

    assert "untrusted data" in server.instructions.lower()
