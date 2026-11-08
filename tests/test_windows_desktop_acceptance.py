"""The desktop release gate requires real, complete named guest evidence."""
import ast
import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / "scripts/smoke-windows-desktop.py"
spec = importlib.util.spec_from_file_location("desktop_acceptance", path)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def complete():
    return [{"name": name, "status": "passed"} for name in sorted(desktop.EXPECTED_INSTALL_CHECKS)]


def test_installer_reports_exact_named_desktop_gate_coverage():
    installer = path.with_name("smoke-install-windows.py")
    tree = ast.parse(installer.read_text(encoding="utf-8"))
    emitted = {node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "passed" and node.args and isinstance(node.args[0], ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            literals = {key.value: value.value for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)}
            if literals.get("status") == "passed" and "name" in literals:
                emitted.add(literals["name"])
    assert emitted == desktop.EXPECTED_INSTALL_CHECKS


def test_current_guest_evidence_includes_separate_pin_discovery_and_reinstall():
    checks = complete()
    desktop.validate_install_checks(checks)
    assert {"separate_pinning_mcp_discovery_30_tools", "sending_disabled_pinning_preserved_20_tools",
            "pinning_disabled_mcp_discovery_18_tools"} <= {check["name"] for check in checks}


@pytest.mark.parametrize("change", ["old_17", "missing_pin", "duplicate", "unknown", "failed", "malformed", "null"])
def test_missing_or_forged_check_coverage_cannot_pass(change):
    checks = complete()
    if change == "old_17":
        checks = checks[:17]
    elif change == "missing_pin":
        checks = [check for check in checks if check["name"] != "separate_pinning_mcp_discovery_30_tools"]
    elif change == "duplicate":
        checks[-1] = checks[0].copy()
    elif change == "unknown":
        checks[-1]["name"] = "a_substitute_with_no_evidence"
    elif change == "failed":
        checks[-1]["status"] = "failed"
    elif change == "malformed":
        checks[-1] = {"name": []}
    else:
        checks = None
    with pytest.raises(RuntimeError, match="required named"):
        desktop.validate_install_checks(checks)
