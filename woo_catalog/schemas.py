"""Versioned JSON contracts used by the CLI and WooCommerce adapter."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Category(CatalogModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    parent_slug: str | None = None
    description: str | None = None
    display: Literal["default", "products", "subcategories", "both"] | None = None
    image: dict | None = None


class Tag(CatalogModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    description: str | None = None


class Attribute(CatalogModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    type: Literal["select"] = "select"
    order_by: Literal["menu_order", "name", "name_num", "id"] = "menu_order"
    has_archives: bool = False
    terms: list[str] = Field(default_factory=list)

    @field_validator("terms")
    @classmethod
    def unique_terms(cls, values: list[str]) -> list[str]:
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"Duplicate attribute term: {', '.join(duplicates)}")
        return values


class ProductCategoryRef(CatalogModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)


class ProductTagRef(CatalogModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)


class ProductAttribute(CatalogModel):
    scope: Literal["global", "local"] = "global"
    name: str = Field(min_length=1)
    slug: str | None = Field(default=None, min_length=1)
    options: list[str] = Field(default_factory=list)
    visible: bool = False
    variation: bool = False
    position: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_scope(self) -> "ProductAttribute":
        if self.scope == "global" and self.slug is None:
            raise ValueError("global product attribute requires slug")
        if self.scope == "local" and self.slug is not None:
            raise ValueError("local product attribute must not contain slug")
        return self


class Product(CatalogModel):
    sku: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    short_description: str | None = None
    slug: str | None = None
    status: Literal["draft", "pending", "private", "publish"] | None = None
    regular_price: str | None = None
    sale_price: str | None = None
    manage_stock: bool | None = None
    stock_quantity: int | None = Field(default=None, ge=0)
    categories: list[ProductCategoryRef] | None = None
    categories_mode: Literal["replace", "merge"] | None = None
    tags: list[ProductTagRef] | None = None
    tags_mode: Literal["replace", "merge"] | None = None
    attributes: list[ProductAttribute] | None = None
    attributes_mode: Literal["replace", "merge"] | None = None

    @field_validator("regular_price", "sale_price", mode="before")
    @classmethod
    def valid_price(cls, value: object) -> object:
        if value is None or value == "":
            return value
        if not isinstance(value, str) or value != value.strip() or re.fullmatch(r"\d+(?:\.\d+)?", value) is None:
            raise ValueError("price must be an empty string or a non-negative decimal string without exponent")
        return value

    @model_validator(mode="after")
    def modes_require_arrays(self) -> "Product":
        for field in ("categories", "tags", "attributes"):
            mode_field = f"{field}_mode"
            if mode_field in self.model_fields_set and field not in self.model_fields_set:
                raise ValueError(f"{mode_field} requires {field}")
        for items, field, label in (
            (self.categories, "slug", "product category slug"),
            (self.tags, "slug", "product tag slug"),
        ):
            values = [getattr(item, field) for item in items or []]
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")
        attribute_keys = [
            (item.scope, item.slug if item.scope == "global" else item.name)
            for item in self.attributes or []
        ]
        duplicate_attributes = sorted(
            {key for key in attribute_keys if attribute_keys.count(key) > 1},
            key=str,
        )
        if duplicate_attributes:
            raise ValueError("Duplicate product attribute identity")
        return self


class _CatalogIdentityValidation:
    @staticmethod
    def _reject_duplicates(items: list[BaseModel], field: str, label: str) -> None:
        values = [getattr(item, field) for item in items]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")

    @classmethod
    def _validate_catalog_identities(
        cls,
        products: list[Product],
        categories: list[Category],
        tags: list[Tag],
        attributes: list[Attribute],
    ) -> None:
        cls._reject_duplicates(products, "sku", "product SKU")
        cls._reject_duplicates(categories, "slug", "category slug")
        cls._reject_duplicates(tags, "slug", "tag slug")
        cls._reject_duplicates(attributes, "slug", "attribute slug")


class CatalogSnapshot(CatalogModel, _CatalogIdentityValidation):
    schema_version: Literal[1]
    categories: list[Category] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicate_identities(self) -> "CatalogSnapshot":
        self._validate_catalog_identities(self.products, self.categories, self.tags, self.attributes)
        return self


class CatalogChange(CatalogModel, _CatalogIdentityValidation):
    """A partial catalog intent that preserves which fields were explicitly supplied."""

    schema_version: Literal[1]
    categories: list[Category] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicate_identities(self) -> "CatalogChange":
        self._validate_catalog_identities(
            self.products or [],
            self.categories or [],
            self.tags or [],
            self.attributes or [],
        )
        return self

    def to_snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            schema_version=1,
            categories=self.categories or [],
            tags=self.tags or [],
            attributes=self.attributes or [],
            products=self.products or [],
        )


class SettingValue(CatalogModel):
    id: str = Field(min_length=1)
    value: object


class SettingsGroup(CatalogModel):
    group_id: str = Field(min_length=1)
    settings: list[SettingValue]


class SettingsChange(CatalogModel):
    schema_version: Literal[1]
    settings: list[SettingsGroup]

    @model_validator(mode="after")
    def no_duplicate_settings(self) -> "SettingsChange":
        group_ids = [group.group_id for group in self.settings]
        duplicate_groups = sorted({value for value in group_ids if group_ids.count(value) > 1})
        if duplicate_groups:
            raise ValueError(f"Duplicate settings group: {', '.join(duplicate_groups)}")
        for group in self.settings:
            setting_ids = [setting.id for setting in group.settings]
            duplicates = sorted({value for value in setting_ids if setting_ids.count(value) > 1})
            if duplicates:
                raise ValueError(
                    f"Duplicate setting id in {group.group_id}: {', '.join(duplicates)}"
                )
        return self


class DeleteAttribute(CatalogModel):
    slug: str = Field(min_length=1)
    terms: list[str] = Field(default_factory=list)


class DeleteRequest(CatalogModel):
    """Explicit identities for deletions; absence never means delete everything."""

    schema_version: Literal[1]
    products: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    attributes: list[DeleteAttribute] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicate_identities(self) -> "DeleteRequest":
        for values, label in ((self.products, "product SKU"), (self.categories, "category slug"), (self.tags, "tag slug")):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")
        attribute_slugs = [attribute.slug for attribute in self.attributes]
        duplicates = sorted({slug for slug in attribute_slugs if attribute_slugs.count(slug) > 1})
        if duplicates:
            raise ValueError(f"Duplicate attribute slug: {', '.join(duplicates)}")
        for attribute in self.attributes:
            duplicates = sorted({value for value in attribute.terms if attribute.terms.count(value) > 1})
            if duplicates:
                raise ValueError(f"Duplicate attribute term for {attribute.slug}: {', '.join(duplicates)}")
        return self


class Operation(CatalogModel):
    resource: str
    identity: str
    action: Literal["create", "update", "delete", "no_op", "skipped_new", "error"]
    status: Literal["planned", "applied", "skipped", "error"]
    message: str | None = None
    details: dict | None = None


class RunReport(CatalogModel):
    mode: Literal["validate", "export", "dry_run", "apply"]
    summary: dict[str, int] = Field(default_factory=dict)
    operations: list[Operation] = Field(default_factory=list)


class ApiError(CatalogModel):
    code: str
    message: str
    path: str | None = None
    hint: str
    retryable: bool = False
