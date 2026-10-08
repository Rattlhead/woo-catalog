import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from woo_catalog.cli import build_parser, main
from woo_catalog.plan_store import PlanStore
from woo_catalog.planning import ExecutionPlan
from woo_catalog.schemas import CatalogSnapshot


def export_snapshot():
    return CatalogSnapshot.model_validate(
        {
            "schema_version": 1,
            "categories": [{"name": "Clothing", "slug": "clothing"}],
            "tags": [{"name": "New", "slug": "new"}],
            "attributes": [{"name": "Color", "slug": "pa_color", "terms": ["Red"]}],
            "products": [{"sku": "OZN-1", "name": "Item"}],
        }
    )


def test_validate_writes_machine_readable_success_report(tmp_path, capsys):
    source = tmp_path / "desired.json"
    output = tmp_path / "validation.json"
    source.write_text(json.dumps({"schema_version": 1, "products": [{"sku": "OZN-1", "name": "Item"}]}), encoding="utf-8")

    exit_code = main(["validate", "--input", str(source), "--output", str(output)])

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["document"] == {"schema_version": 1, "products": [{"sku": "OZN-1", "name": "Item"}]}
    assert result["report"]["mode"] == "validate"
    assert result["report"]["summary"] == {"create": 0, "update": 0, "no_op": 1, "error": 0}
    assert capsys.readouterr().out == f"{output.resolve().as_uri()}\n"


def test_validate_invalid_json_writes_error_without_needing_env(tmp_path):
    source = tmp_path / "invalid.json"
    output = tmp_path / "validation.json"
    source.write_text('{"schema_version": 2}', encoding="utf-8")

    exit_code = main(["validate", "--input", str(source), "--output", str(output)])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["summary"]["error"] == 1
    assert report["operations"][0]["resource"] == "snapshot"


def test_import_defaults_to_sku_matching_without_creating_products():
    args = build_parser().parse_args(["import", "--input", "desired.json", "--output", "report.json"])

    assert args.match_by == "sku"
    assert args.create_products is False
    assert args.verbose is False
    assert args.batch_products is False


def test_diagnose_and_batch_flags_are_explicit_cli_interfaces():
    diagnose = build_parser().parse_args(["diagnose", "--output", "diagnostics.json"])
    importing = build_parser().parse_args([
        "import", "--input", "desired.json", "--output", "plan.json", "--batch-products",
    ])

    assert diagnose.command == "diagnose"
    assert importing.batch_products is True


def test_apply_command_requires_plan_id_and_output():
    args = build_parser().parse_args(["apply", "--plan-id", "abc", "--output", "result.json"])

    assert args.command == "apply"
    assert args.plan_id == "abc"
    assert args.confirm_publish is False


def test_status_command_reads_durable_plan_without_network(tmp_path, capsys):
    now = datetime.now(UTC)
    plan = ExecutionPlan(
        plan_id="plan-1",
        kind="catalog",
        document_sha256="hash",
        store_identity="https://shop.example",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        operations=[],
    )
    state = tmp_path / "state.sqlite3"
    output = tmp_path / "status.json"
    PlanStore(state).save(plan)

    assert main(["status", "--plan-id", "plan-1", "--state-file", str(state), "--output", str(output)]) == 0

    assert json.loads(output.read_text(encoding="utf-8"))["plan_id"] == "plan-1"
    assert capsys.readouterr().out == f"{output.resolve().as_uri()}\n"


def test_verbose_validate_prints_saved_report_contents(tmp_path, capsys):
    source = tmp_path / "desired.json"
    output = tmp_path / "validation.json"
    source.write_text('{"schema_version": 1}', encoding="utf-8")

    assert main(["validate", "--input", str(source), "--output", str(output), "--verbose"]) == 0

    stdout = capsys.readouterr().out
    assert stdout.startswith(f"{output.resolve().as_uri()}\n")
    assert '"mode": "validate"' in stdout


def test_mcp_setup_registers_the_current_python_with_an_absolute_env_file(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("WOO_BASE_URL=https://shop.example\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return SimpleNamespace(returncode=0, stdout="Registered", stderr="")

    monkeypatch.setattr("woo_catalog.mcp_setup.subprocess.run", fake_run)

    assert main(["mcp", "setup", "--env-file", str(env_file)]) == 0

    assert captured["command"][:6] == ["codex", "mcp", "add", "woo-catalog", "--env", f"WOO_CATALOG_ENV_FILE={env_file.resolve()}"]
    assert captured["command"][-2:] == ["-m", "woo_catalog.mcp_server"]
    assert capsys.readouterr().out == "Registered\n"


def test_mcp_setup_requires_existing_env_file(tmp_path, capsys):
    missing = tmp_path / ".env"

    assert main(["mcp", "setup", "--env-file", str(missing)]) == 2

    assert str(missing) in capsys.readouterr().out


def test_categories_import_is_a_separate_command_without_product_flags():
    args = build_parser().parse_args(["categories", "import", "--input", "categories.json", "--output", "report.json"])

    assert args.command == "categories"
    assert args.resource_command == "import"
    assert args.apply is False
    assert not hasattr(args, "create_products")


@pytest.mark.parametrize("resource", ["tags", "attributes"])
def test_other_taxonomy_commands_have_the_same_import_export_contract(resource):
    args = build_parser().parse_args([resource, "export", "--output", f"{resource}.json"])

    assert args.command == resource
    assert args.resource_command == "export"


def test_relative_catalog_export_output_is_scoped_by_site_url(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WOO_BASE_URL", "https://shop.example")
    monkeypatch.setenv("WOO_CONSUMER_KEY", "ck_test")
    monkeypatch.setenv("WOO_CONSUMER_SECRET", "cs_test")
    monkeypatch.setattr("woo_catalog.cli.export_catalog", lambda client: export_snapshot())

    assert main(["export", "--resource", "all", "--output", "catalog"]) == 0

    export_dir = tmp_path / "exports" / "shop.example" / "catalog"
    assert (export_dir / "products.json").exists()
    assert (export_dir / "categories.json").exists()
    assert (export_dir / "tags.json").exists()
    assert (export_dir / "attributes.json").exists()
    assert capsys.readouterr().out == f"{(export_dir / 'export-manifest.json').resolve().as_uri()}\n"


def test_absolute_catalog_export_output_is_used_verbatim(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WOO_BASE_URL", "https://shop.example")
    monkeypatch.setenv("WOO_CONSUMER_KEY", "ck_test")
    monkeypatch.setenv("WOO_CONSUMER_SECRET", "cs_test")
    monkeypatch.setattr("woo_catalog.cli.export_catalog", lambda client: export_snapshot())
    output = tmp_path / "chosen-place"

    assert main(["export", "--resource", "all", "--output", str(output)]) == 0

    assert (output / "products.json").exists()
    assert not (tmp_path / "exports").exists()
    assert capsys.readouterr().out == f"{(output / 'export-manifest.json').resolve().as_uri()}\n"


def test_relative_taxonomy_export_output_is_scoped_by_site_url(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WOO_BASE_URL", "https://shop.example")
    monkeypatch.setenv("WOO_CONSUMER_KEY", "ck_test")
    monkeypatch.setenv("WOO_CONSUMER_SECRET", "cs_test")
    monkeypatch.setattr("woo_catalog.cli.export_categories", lambda client: export_snapshot())

    assert main(["categories", "export", "--output", "categories.json"]) == 0

    output = tmp_path / "exports" / "shop.example" / "categories.json"
    assert output.exists()
    assert capsys.readouterr().out == f"{output.resolve().as_uri()}\n"


def test_relative_settings_export_output_is_scoped_by_site_url(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WOO_BASE_URL", "https://shop.example")
    monkeypatch.setenv("WOO_CONSUMER_KEY", "ck_test")
    monkeypatch.setenv("WOO_CONSUMER_SECRET", "cs_test")
    monkeypatch.setattr("woo_catalog.cli.export_settings", lambda client: {"schema_version": 1, "groups": []})

    assert main(["settings", "export", "--output", "settings.json"]) == 0

    output = tmp_path / "exports" / "shop.example" / "settings.json"
    assert json.loads(output.read_text(encoding="utf-8")) == {"schema_version": 1, "groups": []}
    assert capsys.readouterr().out == f"{output.resolve().as_uri()}\n"


def test_categories_import_does_not_request_or_change_products(tmp_path, monkeypatch):
    class TaxonomyOnlyClient:
        def __init__(self):
            self.paths = []
            self.writes = []
            self.categories = []

        def list_all(self, path, params=None):
            self.paths.append(path)
            if path == "products/categories":
                return [dict(item) for item in self.categories]
            return []

        def post(self, path, payload):
            self.writes.append((path, payload))
            created = {"id": 1, **payload}
            if path == "products/categories":
                self.categories.append(created)
            return created

        def put(self, path, payload):
            self.writes.append((path, payload))
            return {"id": 1, **payload}

    client = TaxonomyOnlyClient()
    monkeypatch.setattr("woo_catalog.service.WooClient", lambda config: client)
    env_file = tmp_path / ".env"
    env_file.write_text("WOO_BASE_URL=https://shop.example\nWOO_CONSUMER_KEY=ck_test\nWOO_CONSUMER_SECRET=cs_test\n", encoding="utf-8")
    source = tmp_path / "categories.json"
    output = tmp_path / "report.json"
    source.write_text('{"schema_version": 1, "categories": [{"name": "Clothing", "slug": "clothing"}]}', encoding="utf-8")

    assert main([
        "categories", "import", "--input", str(source), "--output", str(output),
        "--env-file", str(env_file), "--state-file", str(tmp_path / "state.sqlite3"), "--apply",
    ]) == 0

    assert "products" not in client.paths
    assert client.writes == [("products/categories", {"name": "Clothing", "slug": "clothing"})]
