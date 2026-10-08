"""Register the local stdio server with the Codex CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def register_mcp_server(*, name: str, env_file: Path) -> tuple[int, str]:
    """Register this environment's MCP launcher without editing TOML ourselves."""

    resolved_env_file = env_file.resolve()
    if not resolved_env_file.is_file():
        return 2, f"MCP setup requires an existing env file: {resolved_env_file}"
    command = [
        "codex",
        "mcp",
        "add",
        name,
        "--env",
        f"WOO_CATALOG_ENV_FILE={resolved_env_file}",
        "--",
        sys.executable,
        "-m",
        "woo_catalog.mcp_server",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return 1, "Codex CLI was not found. Install Codex, then run this command again."
    output = (result.stdout or result.stderr).strip()
    if result.returncode:
        return result.returncode, output or f"Codex could not register MCP server '{name}'."
    return 0, output or f"Registered MCP server '{name}'. Restart or refresh MCP connections in Codex."
