"""Export WooCommerce's catalog responses into the versioned JSON contract."""

from __future__ import annotations

from typing import Any

from .client import WooClient
from .schemas import Attribute, CatalogSnapshot, Category, Product, ProductAttribute, ProductCategoryRef, ProductTagRef, Tag


def _optional(value: Any) -> Any:
    return value if value not in (None, "") else None


def export_catalog(client: WooClient) -> CatalogSnapshot:
    categories = export_categories(client).categories
    tags = export_tags(client).tags
    attributes_snapshot = export_attributes(client)
    attributes = attributes_snapshot.attributes
    attributes_by_id = {row["id"]: row for row in client.list_all("products/attributes")}
    products = [
        _to_product(row, attributes_by_id)
        for row in client.list_all("products")
        if row.get("type") == "simple" and row.get("sku")
    ]
    return CatalogSnapshot(schema_version=1, categories=categories, tags=tags, attributes=attributes, products=products)


def export_products(client: WooClient) -> CatalogSnapshot:
    """Export product documents without loading unrelated root taxonomies."""

    rows = [row for row in client.list_all("products") if row.get("type") == "simple" and row.get("sku")]
    global_ids = {
        item.get("id")
        for row in rows
        for item in row.get("attributes", [])
        if item.get("id", 0) != 0
    }
    attributes_by_id = (
        {
            row["id"]: row
            for row in client.list_all("products/attributes")
            if row.get("id") in global_ids
        }
        if global_ids
        else {}
    )
    return CatalogSnapshot(
        schema_version=1,
        products=[_to_product(row, attributes_by_id) for row in rows],
    )


def export_categories(client: WooClient) -> CatalogSnapshot:
    category_rows = client.list_all("products/categories")
    categories_by_id = {row["id"]: row for row in category_rows}
    categories = [
        Category(
            name=row["name"],
            slug=row["slug"],
            parent_slug=categories_by_id.get(row.get("parent"), {}).get("slug"),
            description=_optional(row.get("description")),
            display=_optional(row.get("display")),
            image=_optional(row.get("image")),
        )
        for row in category_rows
    ]

    return CatalogSnapshot(schema_version=1, categories=categories)


def export_tags(client: WooClient) -> CatalogSnapshot:
    tags = [
        Tag(name=row["name"], slug=row["slug"], description=_optional(row.get("description")))
        for row in client.list_all("products/tags")
    ]
    return CatalogSnapshot(schema_version=1, tags=tags)


def export_attributes(client: WooClient) -> CatalogSnapshot:
    attribute_rows = client.list_all("products/attributes")
    attributes = [
        Attribute(
            name=row["name"],
            slug=row["slug"],
            type=row.get("type", "select"),
            order_by=row.get("order_by", "menu_order"),
            has_archives=row.get("has_archives", False),
            terms=[term["name"] for term in client.list_all(f"products/attributes/{row['id']}/terms")],
        )
        for row in attribute_rows
    ]
    return CatalogSnapshot(schema_version=1, attributes=attributes)


def _to_product(row: dict[str, Any], attributes_by_id: dict[int, dict[str, Any]]) -> Product:
    product_attributes = []
    for attribute in row.get("attributes", []):
        common = {
            "name": attribute["name"],
            "options": attribute.get("options", []),
            "visible": attribute.get("visible", False),
            "variation": attribute.get("variation", False),
            "position": attribute.get("position", 0),
        }
        if attribute.get("id", 0) == 0:
            product_attributes.append(ProductAttribute(scope="local", **common))
        elif attribute.get("id") in attributes_by_id:
            product_attributes.append(
                ProductAttribute(scope="global", slug=attributes_by_id[attribute["id"]]["slug"], **common)
            )
    return Product(
        sku=row["sku"],
        name=row.get("name"),
        description=_optional(row.get("description")),
        short_description=_optional(row.get("short_description")),
        slug=_optional(row.get("slug")),
        status=_optional(row.get("status")),
        regular_price=_optional(row.get("regular_price")),
        # WooCommerce represents an intentionally absent sale price as an empty string;
        # retain it so an exported snapshot can clear a sale price on re-import.
        sale_price=row.get("sale_price"),
        manage_stock=row.get("manage_stock"),
        stock_quantity=row.get("stock_quantity"),
        categories=[ProductCategoryRef(name=item["name"], slug=item["slug"]) for item in row.get("categories", [])],
        tags=[ProductTagRef(name=item["name"], slug=item["slug"]) for item in row.get("tags", [])],
        attributes=product_attributes,
    )
