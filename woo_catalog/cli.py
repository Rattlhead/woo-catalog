"""Command-line entry point for validated catalog JSON operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import WooClient
from .config import WooConfig
from .exporter import export_attributes, export_catalog, export_categories, export_tags
from .export_files import write_export_documents
from .mcp_setup import register_mcp_server
from .planning import PlanError
from .schemas import CatalogChange, Operation, RunReport
from .service import CatalogService
from .settings import export_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export and synchronize WooCommerce catalog JSON.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="Validate a catalog JSON file without HTTP requests.")
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--output", required=True, type=Path)
    validate.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")

    diagnose = subcommands.add_parser("diagnose", help="Check documented WordPress and WooCommerce routes without writing.")
    diagnose.add_argument("--output", required=True, type=Path)
    diagnose.add_argument("--env-file", default=Path(".env"), type=Path)
    diagnose.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")

    export = subcommands.add_parser("export", help="Export a resource into its most suitable local document format.")
    export.add_argument("--resource", choices=("products", "categories", "tags", "attributes", "all"), default="all")
    export.add_argument("--output", required=True, type=Path, help="Output file, or directory when --resource all.")
    export.add_argument("--env-file", default=Path(".env"), type=Path)
    export.add_argument("--verbose", action="store_true", help="Print export manifest contents after saving it.")

    importing = subcommands.add_parser("import", help="Plan or apply catalog synchronization.")
    importing.add_argument("--input", required=True, type=Path)
    importing.add_argument("--output", required=True, type=Path)
    import_mode = importing.add_mutually_exclusive_group()
    import_mode.add_argument("--apply", action="store_true", help="Perform POST/PUT requests; otherwise plan only.")
    import_mode.add_argument("--plan", action="store_true", help="Explicitly create a durable plan (the default).")
    importing.add_argument("--env-file", default=Path(".env"), type=Path)
    importing.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
    importing.add_argument("--match-by", choices=("sku",), default="sku", help="Product identity field; SKU is currently supported.")
    importing.add_argument("--create-products", action="store_true", help="Allow creation of SKUs not already in WooCommerce.")
    importing.add_argument("--allow-large-plan", action="store_true")
    importing.add_argument("--batch-products", action="store_true", help="Save an opt-in WooCommerce product batch execution mode in the plan.")
    importing.add_argument("--confirm-publish", action="store_true")
    importing.add_argument("--confirm-large-plan", action="store_true")
    importing.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")

    deleting = subcommands.add_parser("delete", help="Plan or apply explicit catalog deletions.")
    deleting.add_argument("--input", required=True, type=Path)
    deleting.add_argument("--output", required=True, type=Path)
    delete_mode = deleting.add_mutually_exclusive_group()
    delete_mode.add_argument("--apply", action="store_true", help="Perform deletion requests; otherwise plan only.")
    delete_mode.add_argument("--plan", action="store_true", help="Explicitly create a durable plan (the default).")
    deleting.add_argument("--env-file", default=Path(".env"), type=Path)
    deleting.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
    deleting.add_argument("--allow-large-plan", action="store_true")
    deleting.add_argument("--confirm-large-plan", action="store_true")
    deleting.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")

    settings = subcommands.add_parser("settings", help="Export or update WooCommerce settings.")
    settings_commands = settings.add_subparsers(dest="settings_command", required=True)
    settings_export = settings_commands.add_parser("export", help="Export all settings groups.")
    settings_export.add_argument("--output", required=True, type=Path)
    settings_export.add_argument("--env-file", default=Path(".env"), type=Path)
    settings_export.add_argument("--verbose", action="store_true", help="Print document contents after saving it.")
    settings_import = settings_commands.add_parser("import", help="Plan or apply settings changes.")
    settings_import.add_argument("--input", required=True, type=Path)
    settings_import.add_argument("--output", required=True, type=Path)
    settings_mode = settings_import.add_mutually_exclusive_group()
    settings_mode.add_argument("--apply", action="store_true", help="Perform PUT requests; otherwise plan only.")
    settings_mode.add_argument("--plan", action="store_true", help="Explicitly create a durable plan (the default).")
    settings_import.add_argument("--env-file", default=Path(".env"), type=Path)
    settings_import.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
    settings_import.add_argument("--allow-large-plan", action="store_true")
    settings_import.add_argument("--confirm-settings", action="store_true")
    settings_import.add_argument("--confirm-large-plan", action="store_true")
    settings_import.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")

    apply_plan = subcommands.add_parser("apply", help="Apply an existing durable plan exactly once.")
    apply_plan.add_argument("--plan-id", required=True)
    apply_plan.add_argument("--output", required=True, type=Path)
    apply_plan.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
    apply_plan.add_argument("--confirm-publish", action="store_true")
    apply_plan.add_argument("--confirm-settings", action="store_true")
    apply_plan.add_argument("--confirm-permanent", action="store_true")
    apply_plan.add_argument("--confirm-large-plan", action="store_true")
    apply_plan.add_argument("--verbose", action="store_true")

    status = subcommands.add_parser("status", help="Read a durable plan and its execution journal.")
    status.add_argument("--plan-id", required=True)
    status.add_argument("--output", required=True, type=Path)
    status.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
    status.add_argument("--reconcile", action="store_true", help="Read WooCommerce to resolve uncertain writes.")
    status.add_argument("--verbose", action="store_true")

    mcp = subcommands.add_parser("mcp", help="Configure the local MCP server for Codex.")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_setup = mcp_commands.add_parser("setup", help="Register this checkout's MCP server with Codex.")
    mcp_setup.add_argument("--name", default="woo-catalog", help="MCP server name in Codex.")
    mcp_setup.add_argument("--env-file", default=Path(".env"), type=Path, help="Local WooCommerce credentials file.")

    for resource in ("categories", "tags", "attributes"):
        group = subcommands.add_parser(resource, help=f"Import or export {resource} without products.")
        group_commands = group.add_subparsers(dest="resource_command", required=True)
        resource_export = group_commands.add_parser("export", help=f"Export {resource} as a round-trip JSON document.")
        resource_export.add_argument("--output", required=True, type=Path)
        resource_export.add_argument("--env-file", default=Path(".env"), type=Path)
        resource_export.add_argument("--verbose", action="store_true", help="Print document contents after saving it.")
        resource_import = group_commands.add_parser("import", help=f"Import {resource} without reading or changing products.")
        resource_import.add_argument("--input", required=True, type=Path)
        resource_import.add_argument("--output", required=True, type=Path)
        resource_mode = resource_import.add_mutually_exclusive_group()
        resource_mode.add_argument("--apply", action="store_true", help="Perform POST/PUT requests; otherwise plan only.")
        resource_mode.add_argument("--plan", action="store_true", help="Explicitly create a durable plan (the default).")
        resource_import.add_argument("--env-file", default=Path(".env"), type=Path)
        resource_import.add_argument("--state-file", default=Path(".woo-catalog/state.sqlite3"), type=Path)
        resource_import.add_argument("--allow-large-plan", action="store_true")
        resource_import.add_argument("--confirm-large-plan", action="store_true")
        resource_import.add_argument("--verbose", action="store_true", help="Print report contents after saving it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return _validate(args.input, args.output, verbose=args.verbose)
    if args.command == "diagnose":
        return _diagnose(args.output, args.env_file, args.verbose)
    if args.command == "export":
        return _export(args.resource, args.output, args.env_file, verbose=args.verbose)
    if args.command == "delete":
        return _delete(args)
    if args.command == "settings":
        if args.settings_command == "export":
            return _settings_export(args.output, args.env_file, args.verbose)
        return _settings_import(args)
    if args.command == "apply":
        return _apply_plan(args)
    if args.command == "status":
        return _status(args)
    if args.command == "mcp":
        return _mcp_setup(args.name, args.env_file)
    if args.command in {"categories", "tags", "attributes"}:
        if args.resource_command == "export":
            return _taxonomy_export(args.command, args.output, args.env_file, verbose=args.verbose)
        return _taxonomy_import(args.command, args)
    return _import(args)


def _validate(source: Path, output: Path, *, verbose: bool) -> int:
    try:
        document = _load_json(source)
        result = CatalogService.validate_catalog(document)
    except Exception as error:
        _write_json(output, _error_report("snapshot", str(source), str(error)))
        _announce(output, verbose)
        return 2
    _write_json(output, result)
    _announce(output, verbose)
    return 0


def _mcp_setup(name: str, env_file: Path) -> int:
    code, message = register_mcp_server(name=name, env_file=env_file)
    print(message)
    return code


def _diagnose(output: Path, env_file: Path, verbose: bool) -> int:
    try:
        result = CatalogService().diagnose(env_file=env_file)
    except Exception as error:
        _write_json(output, _error_report("diagnose", "store", str(error)))
        _announce(output, verbose)
        return 1
    _write_json(output, result)
    _announce(output, verbose)
    return 0


def _export(resource: str, output: Path, env_file: Path, *, verbose: bool) -> int:
    resolved_output = output
    try:
        config = WooConfig.from_env(env_file)
        resolved_output = _export_output_path(output, config.base_url)
        catalog = export_catalog(WooClient(config))
        result = write_export_documents(catalog, resource=resource, output=resolved_output)
        artifact = Path(result.get("manifest", next(iter(result["documents"].values()))).removeprefix("file://"))
        _announce(artifact, verbose)
        return 0
    except Exception as error:
        error_output = resolved_output / "export-error.json" if resource == "all" else resolved_output
        _write_json(error_output, _error_report("export", "catalog", str(error)))
        _announce(error_output, verbose)
        return 1


def _import(args: argparse.Namespace) -> int:
    try:
        document = CatalogChange.model_validate(_load_json(args.input))
    except Exception as error:
        _write_json(args.output, _error_report("snapshot", str(args.input), str(error)))
        _announce(args.output, args.verbose)
        return 2
    try:
        service = CatalogService(state_path=args.state_file)
        plan = service.plan_catalog(
            document,
            create_products=args.create_products,
            allow_large_plan=args.allow_large_plan,
            batch_products=args.batch_products,
            env_file=args.env_file,
        )
        result = _legacy_apply(service, plan, args, kind="catalog") if args.apply else plan
    except Exception as error:
        _write_json(args.output, _error_report("import", str(args.input), str(error), mode="apply" if args.apply else "dry_run"))
        _announce(args.output, args.verbose)
        return 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _delete(args: argparse.Namespace) -> int:
    try:
        document = _load_json(args.input)
        service = CatalogService(state_path=args.state_file)
        plan = service.plan_delete(document, allow_large_plan=args.allow_large_plan, env_file=args.env_file)
        result = _legacy_apply(service, plan, args, kind="delete") if args.apply else plan
    except Exception as error:
        _write_json(args.output, _error_report("delete", str(args.input), str(error), mode="apply" if args.apply else "dry_run"))
        _announce(args.output, args.verbose)
        return 2 if not args.apply and isinstance(error, (ValueError, json.JSONDecodeError)) else 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _confirm_permanent_deletions(identities: list[str]) -> bool:
    print("The following resources cannot be moved to trash and will be permanently deleted:", file=sys.stderr)
    print(", ".join(identities), file=sys.stderr)
    print("Type 'yes' to continue: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() == "yes"


def _settings_export(output: Path, env_file: Path, verbose: bool) -> int:
    resolved_output = output
    try:
        config = WooConfig.from_env(env_file)
        resolved_output = _export_output_path(output, config.base_url)
        _write_json(resolved_output, export_settings(WooClient(config)))
        _announce(resolved_output, verbose)
        return 0
    except Exception as error:
        _write_json(resolved_output, _error_report("settings", "all", str(error), mode="export"))
        _announce(resolved_output, verbose)
        return 1


def _settings_import(args: argparse.Namespace) -> int:
    try:
        document = _load_json(args.input)
        service = CatalogService(state_path=args.state_file)
        plan = service.plan_settings(document, allow_large_plan=args.allow_large_plan, env_file=args.env_file)
        result = _legacy_apply(service, plan, args, kind="settings") if args.apply else plan
    except Exception as error:
        _write_json(args.output, _error_report("settings", str(args.input), str(error), mode="apply" if args.apply else "dry_run"))
        _announce(args.output, args.verbose)
        return 2 if not args.apply and isinstance(error, (ValueError, json.JSONDecodeError)) else 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _taxonomy_export(resource: str, output: Path, env_file: Path, *, verbose: bool) -> int:
    exporters = {"categories": export_categories, "tags": export_tags, "attributes": export_attributes}
    resolved_output = output
    try:
        config = WooConfig.from_env(env_file)
        resolved_output = _export_output_path(output, config.base_url)
        snapshot = exporters[resource](WooClient(config))
        _write_json(resolved_output, snapshot)
        _announce(resolved_output, verbose)
        return 0
    except Exception as error:
        _write_json(resolved_output, _error_report("export", resource, str(error)))
        _announce(resolved_output, verbose)
        return 1


def _taxonomy_import(resource: str, args: argparse.Namespace) -> int:
    try:
        change = CatalogChange.model_validate(_load_json(args.input))
        _require_only_resource(change, resource)
    except Exception as error:
        _write_json(args.output, _error_report(resource, str(args.input), str(error)))
        _announce(args.output, args.verbose)
        return 2
    try:
        service = CatalogService(state_path=args.state_file)
        plan = service.plan_catalog(change, allow_large_plan=args.allow_large_plan, env_file=args.env_file)
        result = _legacy_apply(service, plan, args, kind="catalog") if args.apply else plan
    except Exception as error:
        _write_json(args.output, _error_report(resource, str(args.input), str(error), mode="apply" if args.apply else "dry_run"))
        _announce(args.output, args.verbose)
        return 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _apply_plan(args: argparse.Namespace) -> int:
    try:
        service = CatalogService(state_path=args.state_file)
        plan = service.store.get(args.plan_id)
        result = _apply_for_kind(service, plan.kind, args.plan_id, args)
    except Exception as error:
        _write_json(args.output, _error_report("plan", args.plan_id, str(error), mode="apply"))
        _announce(args.output, args.verbose)
        return 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _status(args: argparse.Namespace) -> int:
    try:
        result = CatalogService(state_path=args.state_file).operation_status(args.plan_id, reconcile=args.reconcile)
    except Exception as error:
        _write_json(args.output, _error_report("plan", args.plan_id, str(error)))
        _announce(args.output, args.verbose)
        return 1
    _write_json(args.output, result)
    _announce(args.output, args.verbose)
    return _result_exit_code(result)


def _legacy_apply(
    service: CatalogService,
    plan: dict[str, Any],
    args: argparse.Namespace,
    *,
    kind: str,
) -> dict[str, Any]:
    print(
        "warning: --apply creates and applies a saved plan without a separate human review step; "
        "prefer plan followed by 'woo_catalog apply --plan-id ...'",
        file=sys.stderr,
    )
    if kind == "delete" and "confirm_permanent" in plan.get("required_confirmations", []):
        identities = [
            operation["identity"]
            for operation in plan.get("operations", [])
            if "permanent" in operation.get("risks", [])
        ]
        setattr(args, "confirm_permanent", _confirm_permanent_deletions(identities))
    return _apply_for_kind(service, kind, plan["plan_id"], args)


def _apply_for_kind(
    service: CatalogService,
    kind: str,
    plan_id: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if kind == "catalog":
        return service.apply_catalog(
            plan_id,
            confirmed=True,
            confirm_publish=getattr(args, "confirm_publish", False),
            confirm_large_plan=getattr(args, "confirm_large_plan", False),
        )
    if kind == "settings":
        return service.apply_settings(
            plan_id,
            confirmed=True,
            confirm_settings=getattr(args, "confirm_settings", False),
            confirm_large_plan=getattr(args, "confirm_large_plan", False),
        )
    if kind == "delete":
        return service.apply_delete(
            plan_id,
            confirmed=True,
            confirm_permanent=getattr(args, "confirm_permanent", False),
            confirm_large_plan=getattr(args, "confirm_large_plan", False),
        )
    raise PlanError(f"unsupported plan kind: {kind}")


def _result_exit_code(result: dict[str, Any]) -> int:
    state = result.get("state")
    if state in {"partial", "conflict", "uncertain", "failed"}:
        return 1
    summary = result.get("summary", {})
    return 1 if summary.get("error", 0) else 0


def _load_json(source: Path) -> Any:
    return json.loads(source.read_text(encoding="utf-8"))


def _require_only_resource(snapshot: CatalogChange, resource: str) -> None:
    for other in {"categories", "tags", "attributes", "products"} - {resource}:
        if getattr(snapshot, other):
            raise ValueError(f"{resource} import accepts only the '{resource}' array; found '{other}'")


def _export_output_path(output: Path, base_url: str) -> Path:
    if output.is_absolute():
        return output
    host = urlparse(base_url).netloc
    safe_host = "".join(character if character.isalnum() or character in ".-" else "_" for character in host).strip("._")
    return Path("exports") / safe_host / output


def _error_report(resource: str, identity: str, message: str, *, mode: str = "validate") -> RunReport:
    return RunReport(
        mode=mode,
        summary={"create": 0, "update": 0, "no_op": 0, "error": 1},
        operations=[Operation(resource=resource, identity=identity, action="error", status="error", message=message)],
    )


def _write_json(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(model, "model_dump"):
        payload = model.model_dump(exclude_none=True)
    else:
        payload = model
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _announce(path: Path, verbose: bool) -> None:
    """Keep default stdout compact while making the saved artifact discoverable."""
    print(path.resolve().as_uri())
    if verbose:
        print(path.read_text(encoding="utf-8"), end="")
