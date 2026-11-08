"""The desktop release gate requires real, complete named guest evidence."""
import ast
import importlib.util
import subprocess
import zipfile
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / "scripts/smoke-windows-desktop.py"
spec = importlib.util.spec_from_file_location("desktop_acceptance", path)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def complete():
    return [{"name": name, "status": "passed"} for name in sorted(desktop.EXPECTED_INSTALL_CHECKS)]


def test_guest_carries_the_same_uv_archive_into_the_real_installer(tmp_path):
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"reviewed source archive")
    uv_archive = tmp_path / "uv.zip"
    with zipfile.ZipFile(uv_archive, "w") as bundle:
        bundle.writestr("uv/uv.exe", b"inert bootstrap fixture")
    oem = tmp_path / "oem"
    desktop.write_guest(oem, "11", archive, uv_archive)
    assert (oem / "uv.zip").read_bytes() == uv_archive.read_bytes()
    assert (oem / "uv.exe").read_bytes() == b"inert bootstrap fixture"
    bootstrap = (oem / "bootstrap.ps1").read_text(encoding="utf-8-sig")
    guest = (oem / "guest-test.ps1").read_text(encoding="utf-8-sig")
    assert "Copy-Item -LiteralPath 'C:\\OEM\\uv.zip' -Destination $root" in bootstrap
    assert 'scripts/smoke-install-windows.py' in guest
    assert '--uv-archive "$root\\uv.zip"' in guest


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(22, ["curl"]), TimeoutError()])
def test_pinned_uv_transport_failure_uses_official_api_then_verifies_hash(tmp_path, monkeypatch, failure):
    calls = []
    archive = tmp_path / "uv.zip"
    def transfer(url, target, size, **options):
        calls.append((url, options))
        if len(calls) == 1:
            raise failure
        target.write_bytes(b"downloaded fixture")
    monkeypatch.setattr(desktop, "_transfer", transfer)
    monkeypatch.setattr(desktop, "sha256", lambda target: desktop.UV_SHA256)
    desktop.download(desktop.UV_URL, archive, desktop.UV_SHA256)
    assert calls == [(desktop.UV_URL, {}), (desktop.UV_API_URL, {"api_asset": True})]


def test_uv_checksum_mismatch_never_triggers_transport_fallback(tmp_path, monkeypatch):
    calls = []
    def transfer(url, target, size, **options):
        calls.append(url)
        target.write_bytes(b"incorrect bytes")
    monkeypatch.setattr(desktop, "_transfer", transfer)
    monkeypatch.setattr(desktop, "sha256", lambda target: "0" * 64)
    with pytest.raises(RuntimeError, match="SHA-256"):
        desktop.download(desktop.UV_URL, tmp_path / "uv.zip", desktop.UV_SHA256)
    assert calls == [desktop.UV_URL]


def test_other_asset_transport_failure_has_no_uv_fallback(tmp_path, monkeypatch):
    def transfer(*args, **options):
        raise subprocess.CalledProcessError(22, ["curl"])
    monkeypatch.setattr(desktop, "_transfer", transfer)
    with pytest.raises(subprocess.CalledProcessError):
        desktop.download("https://example.invalid/image.iso", tmp_path / "image.iso", "0" * 64)


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
