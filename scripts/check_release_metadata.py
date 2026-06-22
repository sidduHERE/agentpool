#!/usr/bin/env python3
"""Validate release metadata that must move together."""

from __future__ import annotations

import ast
import json
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DISTRIBUTION = "agentpool-cli"
EXPECTED_MCP_NAME = "io.github.sidduHERE/agentpool"
EXPECTED_REPOSITORY = "https://github.com/sidduHERE/agentpool"
EXPECTED_SERVER_NAME = "io.github.sidduHERE/agentpool"
EXPECTED_PYPI_URL = "https://pypi.org/p/agentpool-cli"


def fail(message: str) -> None:
    print(f"release metadata check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def project_metadata() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def package_version() -> str:
    tree = ast.parse((ROOT / "src/agentpool/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    fail("src/agentpool/__init__.py does not define a string __version__")


def main() -> None:
    project = project_metadata()
    version = package_version()
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    if project["name"] != EXPECTED_DISTRIBUTION:
        fail(f"pyproject distribution name must be {EXPECTED_DISTRIBUTION!r}")
    if project["version"] != version:
        fail(f"pyproject version {project['version']!r} != package version {version!r}")

    scripts = project.get("scripts", {})
    expected_entrypoint = "agentpool.cli:app"
    if scripts.get("agentpool") != expected_entrypoint:
        fail("agentpool console script must point at agentpool.cli:app")
    if scripts.get(EXPECTED_DISTRIBUTION) != expected_entrypoint:
        fail("agentpool-cli console script alias must point at agentpool.cli:app")

    if f"mcp-name: {EXPECTED_MCP_NAME}" not in readme:
        fail(f"README.md must contain mcp-name: {EXPECTED_MCP_NAME}")
    if "intentionally omits\npackage entries until `agentpool-cli` exists on PyPI" in readme:
        fail("README.md still says server.json omits PyPI package entries")

    if server["name"] != EXPECTED_SERVER_NAME:
        fail(f"server.json name must be {EXPECTED_SERVER_NAME!r}")
    if server["repository"]["url"] != EXPECTED_REPOSITORY:
        fail(f"server.json repository URL must be {EXPECTED_REPOSITORY!r}")
    if server["version"] != version:
        fail(f"server.json version {server['version']!r} != package version {version!r}")

    packages = server.get("packages")
    if not isinstance(packages, list) or len(packages) != 1:
        fail("server.json must advertise exactly one PyPI package")
    package = packages[0]
    if package.get("registryType") != "pypi":
        fail("server.json package registryType must be pypi")
    if package.get("identifier") != EXPECTED_DISTRIBUTION:
        fail(f"server.json package identifier must be {EXPECTED_DISTRIBUTION!r}")
    if package.get("version") != version:
        fail(f"server.json package version {package.get('version')!r} != {version!r}")
    if package.get("transport", {}).get("type") != "stdio":
        fail("server.json package transport must be stdio")
    if package.get("packageArguments") != [{"type": "positional", "value": "mcp"}]:
        fail("server.json package arguments must run the MCP stdio server")

    required_workflow_text = [
        "vars.PUBLISH_TO_PYPI == 'true'",
        "name: pypi",
        EXPECTED_PYPI_URL,
        "id-token: write",
        "pypa/gh-action-pypi-publish@release/v1",
    ]
    for text in required_workflow_text:
        if text not in workflow:
            fail(f"release workflow missing {text!r}")

    print(f"release metadata ok: {EXPECTED_DISTRIBUTION} {version}")


if __name__ == "__main__":
    main()
