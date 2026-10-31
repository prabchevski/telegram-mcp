"""Portable, fail-closed access to the canonical checked GitHub release feed.

This module deliberately imports only the Python standard library. A scheduled
Windows updater can inspect releases before loading MCP or any native runtime.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import tomllib
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

REPOSITORY = "prabchevski/telegram-mcp"
API = "https://api.github.com/repos/" + REPOSITORY
MAX_DOWNLOAD = 20 * 1024 * 1024
ARCHIVE_NAME = "telegram-mcp-windows.zip"
ARCHIVE_ROOT = "telegram-mcp-macos"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
REVISION_FILE = "SOURCE_REVISION"
WINDOWS_ASSETS_SOURCE = "scripts/windows_assets.py"
DERIVED_FILES = frozenset({
    "install-windows.ps1",
    "plugins/telegram-mcp-work/.codex-plugin/plugin.json",
    "plugins/telegram-mcp-work/.mcp.json",
})
REQUIRED_FILES = frozenset({
    "LICENSE", "pyproject.toml", "uv.lock", "README.md", "AGENTS.md", "GEMINI.md",
    "INSTALL.md", "INSTALL_MACOS.md", "INSTALL_WINDOWS.md", "START_HERE.md", "UNINSTALL_MACOS.md",
    "install-macos.command", "uninstall-macos.command", "scripts/release.py", "scripts/install.py",
    WINDOWS_ASSETS_SOURCE, "src/telegram_search_mcp/__init__.py",
    "src/telegram_search_mcp/server.py", "src/telegram_search_mcp/windows_install.py",
}) | DERIVED_FILES
FORBIDDEN_PARTS = frozenset({
    "profiles", "runtime", "database", "files", "secrets", "credentials", ".ssh", ".env",
    "policy.json", "tdlib.lock", "td.binlog", "td.sqlite", ".DS_Store",
})
ALLOWED_HOSTS = frozenset({
    "api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
})
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class UnpublishedRevisionError(RuntimeError):
    """An installed local revision must not be automatically replaced."""


def version_tuple(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise RuntimeError("Invalid update package version")
    return tuple(map(int, value.split(".")))


def _checked_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        valid = (parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("Update URL or redirect is outside the canonical HTTPS GitHub hosts")


class _CheckedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate BEFORE urllib contacts the redirect target, including each
        # intermediate redirect. A final response URL check alone is too late.
        _checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, *, limit: int, accept: str = "application/vnd.github+json") -> bytes:
    _checked_url(url)
    request = Request(url, headers={"User-Agent": "telegram-mcp-updater", "Accept": accept})
    with build_opener(_CheckedRedirects()).open(request, timeout=30) as response:
        _checked_url(response.url)
        declared = response.headers.get("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            raise RuntimeError("Update download exceeds the size limit")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Update download exceeds the size limit")
    return data


def _json(url: str) -> dict:
    value = json.loads(_download(url, limit=2 * 1024 * 1024))
    if not isinstance(value, dict):
        raise RuntimeError("Unexpected GitHub response")
    return value


def _revision(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise RuntimeError("GitHub returned an invalid revision")
    return value


def _tag_revision(tag: str) -> str:
    obj = _json(API + "/git/ref/tags/" + tag).get("object")
    for _ in range(5):
        if not isinstance(obj, dict):
            raise RuntimeError("GitHub returned an invalid release tag")
        sha = _revision(obj.get("sha"))
        if obj.get("type") == "commit":
            return sha
        if obj.get("type") != "tag":
            raise RuntimeError("A release tag does not identify a commit")
        obj = _json(API + "/git/tags/" + sha).get("object")
    raise RuntimeError("Too many nested release tags")


def _release_asset(release: dict, name: str, tag: str) -> dict:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("Invalid release assets")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError("Published release is missing a unique Windows asset or checksum")
    asset = matches[0]
    asset_id = asset.get("id")
    size = asset.get("size")
    if (type(asset_id) is not int or asset_id <= 0 or type(size) is not int or size <= 0
            or asset.get("state") != "uploaded"
            or asset.get("url") != API + "/releases/assets/" + str(asset_id)
            or asset.get("browser_download_url") != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"):
        raise RuntimeError("Release asset is outside the exact canonical release")
    if size > (4096 if name.endswith(".sha256") else MAX_DOWNLOAD):
        raise RuntimeError("Published release asset exceeds the size limit")
    digest = asset.get("digest")
    if digest is not None and (not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        raise RuntimeError("Invalid GitHub release asset digest")
    return {key: asset.get(key) for key in ("id", "name", "url", "browser_download_url", "size", "digest", "state")}


def checked_release() -> dict | None:
    """Return only a published release at main with successful exact-SHA CI."""
    revision = _revision(_json(API + "/commits/main").get("sha"))
    query = urlencode({"branch": "main", "event": "push", "head_sha": revision, "status": "success", "per_page": 10})
    runs = _json(API + "/actions/workflows/ci.yml/runs?" + query).get("workflow_runs")
    if not isinstance(runs, list):
        raise RuntimeError("Invalid CI results")
    successful = any(
        isinstance(run, dict) and run.get("head_sha") == revision and run.get("head_branch") == "main"
        and run.get("event") == "push" and run.get("status") == "completed" and run.get("conclusion") == "success"
        and isinstance(run.get("head_repository"), dict) and run["head_repository"].get("full_name") == REPOSITORY
        and run.get("path") == ".github/workflows/ci.yml"
        for run in runs
    )
    if not successful:
        return None
    try:
        release = _json(API + "/releases/latest")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    tag = release.get("tag_name")
    if (not isinstance(tag, str) or not tag.startswith("v") or not SEMVER.fullmatch(tag[1:])
            or release.get("draft") is not False or release.get("prerelease") is not False
            or not isinstance(release.get("published_at"), str) or not release["published_at"]
            or type(release.get("id")) is not int or release["id"] <= 0
            or release.get("url") != API + "/releases/" + str(release["id"])):
        raise RuntimeError("Invalid published canonical release")
    if _tag_revision(tag) != revision:
        return None
    return {"revision": revision, "version": tag[1:], "tag": tag, "release_id": release["id"],
            "archive": _release_asset(release, ARCHIVE_NAME, tag),
            "checksum": _release_asset(release, ARCHIVE_NAME + ".sha256", tag)}


def ensure_forward(installed_revision: object, candidate_revision: object) -> None:
    if (not isinstance(installed_revision, str) or not SHA.fullmatch(installed_revision)):
        raise UnpublishedRevisionError(
            "The installed revision is unknown or local. Include local fixes in the canonical repository, "
            "then install a verified published release once before enabling automatic replacement."
        )
    candidate_revision = _revision(candidate_revision)
    try:
        comparison = _json(API + "/compare/" + installed_revision + "..." + candidate_revision)
    except HTTPError as exc:
        if exc.code == 404:
            raise UnpublishedRevisionError(
                "The installed revision is unpublished. Automatic replacement stopped to preserve local fixes."
            ) from None
        raise
    if comparison.get("status") not in {"ahead", "identical"}:
        raise RuntimeError("Automatic downgrade or divergent update refused")


def _asset_bytes(asset: dict, *, limit: int) -> bytes:
    # An immutable asset ID ties the download to the inspected release. A
    # mutable /download/tag/name link alone could race an asset replacement.
    payload = _download(asset["url"], limit=limit, accept="application/octet-stream")
    if len(payload) != asset["size"]:
        raise RuntimeError("Downloaded release asset has an unexpected size")
    if asset.get("digest") and "sha256:" + hashlib.sha256(payload).hexdigest() != asset["digest"]:
        raise RuntimeError("Downloaded release asset differs from its GitHub digest")
    return payload


def download_release(candidate: dict) -> bytes:
    tag = candidate.get("tag")
    version_tuple(candidate.get("version"))
    _revision(candidate.get("revision"))
    if tag != "v" + candidate["version"] or type(candidate.get("release_id")) is not int:
        raise RuntimeError("Invalid checked release candidate")
    # Revalidate IDs/URLs even when callers saved the candidate as JSON.
    release = {"assets": [candidate.get("archive"), candidate.get("checksum")]}
    archive = _release_asset(release, ARCHIVE_NAME, tag)
    checksum = _release_asset(release, ARCHIVE_NAME + ".sha256", tag)
    checksum_data = _asset_bytes(checksum, limit=4096)
    try:
        checksum_text = checksum_data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Invalid release checksum") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  " + re.escape(ARCHIVE_NAME) + r"\n?", checksum_text)
    if match is None:
        raise RuntimeError("Release checksum does not identify the exact Windows asset")
    payload = _asset_bytes(archive, limit=MAX_DOWNLOAD)
    if hashlib.sha256(payload).hexdigest() != match[1]:
        raise RuntimeError("Windows release archive checksum verification failed")
    return payload


def _allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name
            or name != path.as_posix()
            or any(part.casefold() in {item.casefold() for item in FORBIDDEN_PARTS}
                   or part.casefold().startswith(".env.") for part in path.parts)):
        return False
    for part in path.parts:
        if (part.endswith((".", " ")) or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            return False
    if name in REQUIRED_FILES or name in {".gitignore", "NOTICE", "update-macos.command", REVISION_FILE}:
        return True
    if len(path.parts) == 1 and path.suffix == ".md":
        return True
    if path.parts[:2] == ("src", "telegram_search_mcp") and path.suffix == ".py":
        return True
    if path.parts[0] == "tests" and path.suffix == ".py":
        return path.name.startswith("test_") or path.name in {"conftest.py", "__init__.py"}
    if path.parts[0] == "scripts" and path.suffix in {".py", ".sh"}:
        return True
    return path.parts[:2] == (".github", "workflows") and path.suffix in {".yml", ".yaml"}


def validate_release_archive(payload: bytes, version: str, *, revision: str | None = None) -> dict[str, bytes]:
    """Validate every ZIP member and the generated Windows assets before writes.

    Candidate Python files are never imported or executed during this audit.
    The returned source excludes generated files and the distribution manifest,
    matching the installer's Git source inventory.
    """
    version_tuple(version)
    if len(payload) > MAX_DOWNLOAD:
        raise RuntimeError("Update archive is too large")
    files: dict[str, bytes] = {}
    case_names: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_DOWNLOAD:
            raise RuntimeError("Update archive is too large")
        for item in entries:
            prefix = ARCHIVE_ROOT + "/"
            if not item.filename.startswith(prefix):
                raise RuntimeError("Unexpected update archive root")
            name = item.filename[len(prefix):]
            if (not name or item.is_dir() or (name != MANIFEST_NAME and not _allowed_path(name))
                    or name.casefold() in case_names or item.flag_bits & 1 or item.file_size > 5 * 1024 * 1024):
                raise RuntimeError("Unsafe, unexpected, duplicate, or oversized update archive member")
            mode = item.external_attr >> 16
            wanted_mode = 0o755 if name.endswith((".command", ".sh")) else 0o644
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != wanted_mode:
                raise RuntimeError("Update archive contains an unexpected file type or mode")
            data = archive.read(item)
            if b"\0" in data:
                raise RuntimeError("Binary data in source update")
            data.decode("utf-8")
            files[name] = data
            case_names.add(name.casefold())
    for name in files:
        if any("/".join(PurePosixPath(name).parts[:index]).casefold() in case_names
               for index in range(1, len(PurePosixPath(name).parts))):
            raise RuntimeError("Update archive has overlapping file and directory paths")
    try:
        manifest = json.loads(files.pop(MANIFEST_NAME))
        project = tomllib.loads(files["pyproject.toml"].decode())["project"]
        if project.get("name") != "telegram-search-mcp" or project.get("version") != version:
            raise RuntimeError("Release package and version differ from the checked tag")
        init = files["src/telegram_search_mcp/__init__.py"].decode()
        if not re.search(rf'__version__\s*=\s*[\"\']{re.escape(version)}[\"\']', init):
            raise RuntimeError("Release package versions disagree")
        if REQUIRED_FILES - files.keys():
            raise RuntimeError("Update archive is missing required source or Windows assets")
        expected = {"format": 1, "package": "telegram-search-mcp", "version": version,
                    "archive_root": ARCHIVE_ROOT,
                    "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                               "mode": "0755" if name.endswith((".command", ".sh")) else "0644"}
                              for name, data in sorted(files.items())]}
        if manifest != expected:
            raise RuntimeError("Update archive differs from its release manifest")
        if revision is not None:
            _revision(revision)
            if files.get(REVISION_FILE) != (revision + "\n").encode("ascii"):
                raise RuntimeError("Release source revision differs from the checked commit")
        elif REVISION_FILE in files:
            _revision(files[REVISION_FILE].decode("ascii").strip())
        nodes = ast.parse(files[WINDOWS_ASSETS_SOURCE].decode()).body
        if nodes and isinstance(nodes[0], ast.Expr) and isinstance(nodes[0].value, ast.Constant) and isinstance(nodes[0].value.value, str):
            nodes = nodes[1:]
        if (len(nodes) != 1 or not isinstance(nodes[0], ast.Assign) or len(nodes[0].targets) != 1
                or not isinstance(nodes[0].targets[0], ast.Name) or nodes[0].targets[0].id != "WINDOWS_ASSETS"):
            raise RuntimeError("Invalid reviewed Windows assets source")
        literals = ast.literal_eval(nodes[0].value)
        if (not isinstance(literals, dict) or literals.keys() != DERIVED_FILES
                or not all(isinstance(item, str) for item in literals.values())):
            raise RuntimeError("Unexpected generated Windows assets")
        generated = {name: text.replace("@PACKAGE_VERSION@", version).encode() for name, text in literals.items()}
        if {name: files[name] for name in DERIVED_FILES} != generated:
            raise RuntimeError("Generated Windows assets differ from their reviewed source")
    except (KeyError, ValueError, SyntaxError, TypeError) as exc:
        raise RuntimeError("Update archive lacks a valid release inventory") from exc
    return {name: data for name, data in files.items() if name not in DERIVED_FILES and name != REVISION_FILE}
