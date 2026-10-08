"""Choose a lossless local document format for each catalog export resource."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .schemas import CatalogSnapshot


ExportResource = Literal["products", "categories", "tags", "attributes", "all"]


def write_export_documents(snapshot: CatalogSnapshot, *, resource: ExportResource, output: Path) -> dict[str, object]:
    """Write one resource, or four independent documents plus a manifest.

    Every resource is JSON so the file can be imported back without losing
    category settings or attribute terms.
    """
    if resource == "all":
        output.mkdir(parents=True, exist_ok=True)
        documents: dict[str, str] = {}
        for name, filename in (("products", "products.json"), ("categories", "categories.json"), ("tags", "tags.json"), ("attributes", "attributes.json")):
            documents.update(write_export_documents(snapshot, resource=name, output=output / filename)["documents"])
        manifest_path = output / "export-manifest.json"
        _write_json(manifest_path, {"documents": documents})
        return {"manifest": manifest_path.resolve().as_uri(), "documents": documents}

    expected_extension = ".json"
    if output.suffix.lower() != expected_extension:
        raise ValueError(f"{resource} export must use a {expected_extension} output file")
    output.parent.mkdir(parents=True, exist_ok=True)
    if resource == "categories":
        _write_json(output, CatalogSnapshot(schema_version=1, categories=snapshot.categories).model_dump(exclude_none=True))
    elif resource == "tags":
        _write_json(output, CatalogSnapshot(schema_version=1, tags=snapshot.tags).model_dump(exclude_none=True))
    elif resource == "products":
        _write_json(output, CatalogSnapshot(schema_version=1, products=snapshot.products).model_dump(exclude_none=True))
    elif resource == "attributes":
        _write_json(output, CatalogSnapshot(schema_version=1, attributes=snapshot.attributes).model_dump(exclude_none=True))
    else:
        raise ValueError(f"Unsupported export resource: {resource}")
    return {"documents": {resource: output.resolve().as_uri()}}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
