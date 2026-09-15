"""Daily, opt-out updates from the canonical main branch after successful CI."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import zipfile

from .config_io import atomic_write, read_source, validate_path
from .installation import RECEIPT, install, read_receipt, safe_environment
from .launchers import current_version, installed_root, validate_root
from .registration import ALIASES, NAMES, _load, default_path, expected_entry, real_home

REPOSITORY = "prabchevski/telegram-search-mcp"
API = "https://api.github.com/repos/" + REPOSITORY
CHECK_INTERVAL = 86400
MAX_DOWNLOAD = 20 * 1024 * 1024
LABEL_PREFIX = "io.github.prabchevski.telegram-search-mcp.update."


def _download(url: str, *, limit: int) -> bytes:
    allowed_hosts = {"api.github.com", "codeload.github.com"}
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise RuntimeError("Update URL is outside the canonical GitHub hosts")
    request = Request(url, headers={"User-Agent": "telegram-search-mcp-updater", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=30) as response:
        redirected = urlparse(response.url)
        if redirected.scheme != "https" or redirected.hostname not in allowed_hosts:
            raise RuntimeError("Unexpected update download redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Update download exceeds the size limit")
    return data


def _json(url: str) -> dict:
    data = json.loads(_download(url, limit=2 * 1024 * 1024))
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected GitHub response")
    return data


def checked_revision() -> str | None:
    commit = _json(API + "/commits/main").get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("GitHub returned an invalid main revision")
    query = urlencode({"branch": "main", "event": "push", "head_sha": commit, "status": "success", "per_page": 10})
    runs = _json(API + "/actions/workflows/ci.yml/runs?" + query).get("workflow_runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("Invalid CI results")
    for run in runs:
        if (isinstance(run, dict) and run.get("head_sha") == commit and run.get("head_branch") == "main"
                and run.get("event") == "push" and run.get("status") == "completed"
                and run.get("conclusion") == "success"
                and run.get("head_repository", {}).get("full_name") == REPOSITORY
                and run.get("path") == ".github/workflows/ci.yml"):
            return commit
    return None


def extract_source(payload: bytes, revision: str, destination: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Invalid update revision")
    prefix = "telegram-search-mcp-" + revision + "/"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_DOWNLOAD:
            raise RuntimeError("Update archive is too large")
        files: dict[str, bytes] = {}
        for item in entries:
            if not item.filename.startswith(prefix):
                raise RuntimeError("Unexpected update archive root")
            name = item.filename[len(prefix):]
            if not name and item.is_dir():
                continue
            path = PurePosixPath(name.rstrip("/"))
            if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or path.as_posix() != name.rstrip("/"):
                raise RuntimeError("Unsafe update archive path")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise RuntimeError("Update archive contains a special file")
            if item.is_dir():
                continue
            allowed = (
                len(path.parts) == 1 and (path.suffix in (".md", ".command") or name in {"LICENSE", "NOTICE", "pyproject.toml", "uv.lock", ".gitignore"})
                or path.parts[:2] == ("src", "telegram_search_mcp") and path.suffix == ".py"
                or path.parts[0] in ("scripts", "tests") and path.suffix in (".py", ".sh")
                or path.parts[:2] == (".github", "workflows") and path.suffix in (".yml", ".yaml")
            )
            if not allowed or name in files or item.file_size > 5 * 1024 * 1024:
                raise RuntimeError("Unexpected or duplicate file in update archive")
            data = archive.read(item)
            if b"\0" in data:
                raise RuntimeError("Binary data in source update")
            data.decode("utf-8")
            files[name] = data
        required = {"LICENSE", "pyproject.toml", "uv.lock", "README.md", "install-macos.command", "uninstall-macos.command", "src/telegram_search_mcp/updater.py", "src/telegram_search_mcp/server.py"}
        if required - files.keys():
            raise RuntimeError("Update archive is missing required source files")
        # Validate the whole archive before writing any part of it.
        destination.mkdir(mode=0o700)
        for name, content in files.items():
            target = destination / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o700 if target.suffix in (".command", ".sh") else 0o600)
        (destination / "SOURCE_REVISION").write_text(revision + "\n")
    return destination


def schedule_label(root: Path) -> str:
    return LABEL_PREFIX + hashlib.sha256(str(root).encode()).hexdigest()[:12]


def schedule_definition(root: Path) -> dict:
    return {"Label": schedule_label(root),
            "ProgramArguments": ["/bin/sh", str(root / "update.command"), "--scheduled", "--install-root", str(root)],
            "StartInterval": CHECK_INTERVAL, "RunAtLoad": True, "ProcessType": "Background",
            "StandardOutPath": str(root / "updates.log"), "StandardErrorPath": str(root / "updates.log")}


def configure_schedule(root: Path, enabled: bool) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Automatic updates require macOS launchd")
    validate_root(root)
    definition = schedule_definition(root)
    path = real_home() / "Library/LaunchAgents" / (definition["Label"] + ".plist")
    before = read_source(path)
    if before is not None:
        old = plistlib.loads(before)
        if old.get("Label") != definition["Label"] or old.get("ProgramArguments") != definition["ProgramArguments"]:
            raise RuntimeError("An unrelated LaunchAgent uses the updater name; it was not changed")
    domain = f"gui/{os.getuid()}"
    if not enabled:
        if before is not None:
            subprocess.run(["/bin/launchctl", "bootout", domain + "/" + definition["Label"]], capture_output=True, check=False)
            if read_source(path) != before:
                raise RuntimeError("Updater schedule changed concurrently")
            path.unlink()
        return
    from .service import open_private_file
    descriptor = open_private_file(root / "updates.log")
    os.close(descriptor)
    content = plistlib.dumps(definition)
    if before == content:
        running = subprocess.run(["/bin/launchctl", "print", domain + "/" + definition["Label"]], capture_output=True, check=False)
        if running.returncode == 0:
            return
    else:
        atomic_write(path, content, expected=before)
        if before is not None:
            subprocess.run(["/bin/launchctl", "bootout", domain + "/" + definition["Label"]], capture_output=True, check=False)
    result = subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("Update schedule was saved but could not start. Log out and back in, or run tgsearch updates on in your desktop session")


def set_enabled(root: Path, enabled: bool) -> None:
    from .installation import installation_lock
    with installation_lock(root):
        data = read_receipt(root)
        if not data:
            raise RuntimeError("Install this version once before configuring updates")
        configure_schedule(root, enabled)
        data["auto_update"] = enabled
        atomic_write(root / RECEIPT, (json.dumps(data, indent=2) + "\n").encode(), expected=read_source(root / RECEIPT))


def remember_clients(root: Path, paths: dict[str, Path]) -> None:
    from .installation import installation_lock
    with installation_lock(root):
        data = read_receipt(root)
        if not data:
            return
        data["clients"].update({client: str(path) for client, path in paths.items()})
        atomic_write(root / RECEIPT, (json.dumps(data, indent=2) + "\n").encode(), expected=read_source(root / RECEIPT))


def forget_clients(root: Path, clients: list[str], paths: dict[str, Path] | None = None) -> None:
    from .installation import installation_lock
    with installation_lock(root):
        data = read_receipt(root)
        if not data:
            return
        for client in clients:
            if paths is None or data["clients"].get(client) == str(paths[client]):
                data["clients"].pop(client, None)
        if not data["clients"]:
            if data.get("auto_update"):
                configure_schedule(root, False)
            data["auto_update"] = False
        atomic_write(root / RECEIPT, (json.dumps(data, indent=2) + "\n").encode(), expected=read_source(root / RECEIPT))


def find_uv() -> str:
    for path in (Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError("uv is missing; run the installer to restore dependencies")


def update(root: Path, *, scheduled: bool = False, check_only: bool = False) -> dict:
    receipt = read_receipt(root)
    receipt_source = read_source(root / RECEIPT)
    if not receipt:
        raise RuntimeError("Install the current package once to enable managed updates")
    if scheduled and not receipt.get("auto_update"):
        return {"status": "disabled"}
    check_file = root / "last-update-check.json"
    if scheduled:
        previous_check = read_source(check_file)
        if previous_check is not None:
            previous_time = json.loads(previous_check).get("time", 0)
            if 0 <= time.time() - previous_time < CHECK_INTERVAL:
                return {"status": "not_due"}
        atomic_write(check_file, (json.dumps({"time": time.time()}) + "\n").encode(), expected=previous_check)
    candidate = checked_revision()
    if candidate is None:
        return {"status": "waiting_for_ci"}
    if candidate == receipt.get("revision"):
        return {"status": "up_to_date", "revision": candidate}
    if receipt.get("revision"):
        comparison = _json(API + "/compare/" + receipt["revision"] + "..." + candidate)
        if comparison.get("status") not in ("ahead", "identical"):
            raise RuntimeError("Automatic downgrade or divergent update refused")
    if check_only:
        return {"status": "available", "revision": candidate}
    version = current_version(root)
    # An unregistered or edited launcher is a user decision, not something a
    # background update should silently undo.
    registration_snapshot = {}
    for client, value in receipt["clients"].items():
        source = read_source(Path(value))
        config = _load(client, Path(value), source)
        entry = config.get(NAMES[client], {}).get(ALIASES[client])
        wanted = expected_entry(client, str(version / ".venv/bin/python"), root)
        if entry != wanted:
            return {"status": "registration_changed", "client": client}
        registration_snapshot[client] = source
    if not receipt["clients"]:
        return {"status": "no_registered_clients"}
    uv = find_uv()
    payload = _download("https://codeload.github.com/" + REPOSITORY + "/zip/" + candidate, limit=MAX_DOWNLOAD)
    with tempfile.TemporaryDirectory(prefix="telegram-update-", dir=root) as temporary:
        source = extract_source(payload, candidate, Path(temporary) / "source")
        import tomllib
        project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
        def version_tuple(value):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
                raise RuntimeError("Invalid update package version")
            return tuple(map(int, value.split(".")))
        if version_tuple(project.get("version")) < version_tuple(receipt["version"]):
            raise RuntimeError("Automatic version downgrade refused")
        install(source, root, uv=uv, python=str(version / ".venv/bin/python"), clients=list(receipt["clients"]),
                paths={client: Path(path) for client, path in receipt["clients"].items()}, auto_update="keep", revision=candidate,
                registration_snapshot=registration_snapshot, expected_receipt=receipt_source)
    return {"status": "updated", "revision": candidate, "activation": "new clients and the next service start"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.install_root or installed_root()
    if root is None:
        raise SystemExit("Run the updater from an installed copy")
    try:
        if args.scheduled:
            from .service import open_private_file
            descriptor = open_private_file(root / "updates.log")
            try:
                if os.fstat(descriptor).st_size > 64 * 1024:
                    os.ftruncate(descriptor, 0)
            finally:
                os.close(descriptor)
        result = update(root, scheduled=args.scheduled, check_only=args.check)
        if result["status"] not in ("not_due", "disabled", "up_to_date", "waiting_for_ci") or not args.scheduled:
            print(json.dumps(result))
    except Exception as exc:
        print(f"Telegram Search update failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
