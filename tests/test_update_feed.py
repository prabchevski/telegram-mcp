"""The portable release feed accepts only exact canonical published CI output."""
import hashlib
import io
import importlib.util
import json
import stat
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
import zipfile

import pytest

from telegram_search_mcp import update_feed as feed

REVISION = "a" * 40
PREVIOUS = "b" * 40
VERSION = "0.11.0"


def release_files():
    files = {name: b"reviewed source\n" for name in feed.REQUIRED_FILES}
    files["pyproject.toml"] = f'[project]\nname = "telegram-search-mcp"\nversion = "{VERSION}"\n'.encode()
    files["src/telegram_search_mcp/__init__.py"] = f'__version__ = "{VERSION}"\n'.encode()
    assets = {name: "reviewed @PACKAGE_VERSION@\n" for name in feed.DERIVED_FILES}
    files[feed.WINDOWS_ASSETS_SOURCE] = ('"""Reviewed Windows assets."""\nWINDOWS_ASSETS = ' + repr(assets) + '\n').encode()
    files.update({name: text.replace("@PACKAGE_VERSION@", VERSION).encode() for name, text in assets.items()})
    return files


def archive_bytes(files=None, *, member_changes=None, add_entries=()):
    files = release_files() if files is None else files.copy()
    manifest = {"format": 1, "package": "telegram-search-mcp", "version": VERSION,
                "archive_root": feed.ARCHIVE_ROOT,
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                           "mode": "0755" if name.endswith((".command", ".sh")) else "0644"}
                          for name, data in sorted(files.items())]}
    files[feed.MANIFEST_NAME] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(feed.ARCHIVE_ROOT + "/" + name)
            info.external_attr = (stat.S_IFREG | (0o755 if name.endswith((".command", ".sh")) else 0o644)) << 16
            info.create_system = 3
            if member_changes:
                member_changes(info)
            archive.writestr(info, content)
        for name, content in add_entries:
            info = zipfile.ZipInfo(feed.ARCHIVE_ROOT + "/" + name)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
    return output.getvalue()


def published_release(payload=b"zip"):
    checksum = (hashlib.sha256(payload).hexdigest() + "  " + feed.ARCHIVE_NAME + "\n").encode()
    assets = []
    for index, name, data in [(2, feed.ARCHIVE_NAME, payload), (3, feed.ARCHIVE_NAME + ".sha256", checksum)]:
        assets.append({"id": index, "name": name, "size": len(data), "state": "uploaded",
                       "url": feed.API + "/releases/assets/" + str(index),
                       "browser_download_url": f"https://github.com/{feed.REPOSITORY}/releases/download/v{VERSION}/{name}",
                       "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    return {"id": 1, "url": feed.API + "/releases/1", "tag_name": "v" + VERSION,
            "draft": False, "prerelease": False, "published_at": "2026-10-13T12:00:00Z", "assets": assets}, checksum


def successful_run():
    return {"head_sha": REVISION, "head_branch": "main", "event": "push", "status": "completed",
            "conclusion": "success", "head_repository": {"full_name": feed.REPOSITORY},
            "path": ".github/workflows/ci.yml"}


def fake_feed(monkeypatch, *, run=None, release=None, tag_sha=REVISION):
    release = published_release()[0] if release is None else release
    def github_json(url):
        if url == feed.API + "/commits/main":
            return {"sha": REVISION}
        if "/actions/workflows/ci.yml/runs?" in url:
            assert "head_sha=" + REVISION in url
            return {"workflow_runs": [successful_run() if run is None else run]}
        if url == feed.API + "/releases/latest":
            return release
        if url == feed.API + "/git/ref/tags/v" + VERSION:
            return {"object": {"type": "commit", "sha": tag_sha}}
        raise AssertionError(url)
    monkeypatch.setattr(feed, "_json", github_json)


def test_checked_release_uses_exact_main_ci_and_tag(monkeypatch):
    fake_feed(monkeypatch)
    candidate = feed.checked_release()
    assert candidate["revision"] == REVISION
    assert candidate["version"] == VERSION
    assert candidate["archive"]["name"] == "telegram-mcp-windows.zip"


@pytest.mark.parametrize(("key", "value"), [
    ("head_sha", PREVIOUS), ("head_branch", "feature"), ("event", "pull_request"),
    ("status", "in_progress"), ("conclusion", "failure"), ("path", ".github/workflows/fake.yml"),
    ("head_repository", {"full_name": "other/telegram-mcp"}), ("head_repository", None),
])
def test_unapproved_ci_does_not_offer_an_update(monkeypatch, key, value):
    run = successful_run()
    run[key] = value
    fake_feed(monkeypatch, run=run)
    assert feed.checked_release() is None


def test_published_release_must_resolve_to_same_exact_revision(monkeypatch):
    fake_feed(monkeypatch, tag_sha=PREVIOUS)
    assert feed.checked_release() is None


def test_annotated_release_tag_is_resolved(monkeypatch):
    fake_feed(monkeypatch)
    original = feed._json
    def github_json(url):
        if "/git/ref/tags/" in url:
            return {"object": {"type": "tag", "sha": PREVIOUS}}
        if url == feed.API + "/git/tags/" + PREVIOUS:
            return {"object": {"type": "commit", "sha": REVISION}}
        return original(url)
    monkeypatch.setattr(feed, "_json", github_json)
    assert feed.checked_release()["revision"] == REVISION


@pytest.mark.parametrize(("key", "value"), [("draft", True), ("prerelease", True), ("published_at", None),
                                            ("tag_name", "main"), ("url", "https://github.com/other/release")])
def test_unpublished_or_noncanonical_release_rejected(monkeypatch, key, value):
    release = published_release()[0]
    release[key] = value
    fake_feed(monkeypatch, release=release)
    with pytest.raises(RuntimeError, match="published canonical"):
        feed.checked_release()


def test_missing_release_waits(monkeypatch):
    fake_feed(monkeypatch)
    original = feed._json
    def github_json(url):
        if url.endswith("/releases/latest"):
            raise HTTPError(url, 404, "not found", {}, None)
        return original(url)
    monkeypatch.setattr(feed, "_json", github_json)
    assert feed.checked_release() is None


@pytest.mark.parametrize("status", ["behind", "diverged", "unknown"])
def test_refuses_downgrade_or_divergence(monkeypatch, status):
    monkeypatch.setattr(feed, "_json", lambda _: {"status": status})
    with pytest.raises(RuntimeError, match="downgrade or divergent"):
        feed.ensure_forward(PREVIOUS, REVISION)


@pytest.mark.parametrize("revision", [None, "local", "a" * 39])
def test_unknown_local_revision_is_preserved(monkeypatch, revision):
    monkeypatch.setattr(feed, "_json", lambda _: pytest.fail("Unknown local revision must not contact compare"))
    with pytest.raises(feed.UnpublishedRevisionError, match="local fixes"):
        feed.ensure_forward(revision, REVISION)


def test_unpublished_revision_is_preserved(monkeypatch):
    def missing(url):
        raise HTTPError(url, 404, "not found", {}, None)
    monkeypatch.setattr(feed, "_json", missing)
    with pytest.raises(feed.UnpublishedRevisionError, match="unpublished"):
        feed.ensure_forward(PREVIOUS, REVISION)


def test_download_binds_checksum_and_digest_to_exact_asset_ids(monkeypatch):
    payload = archive_bytes()
    release, checksum = published_release(payload)
    fake_feed(monkeypatch, release=release)
    candidate = feed.checked_release()
    requests = []
    def download(url, *, limit, accept):
        requests.append((url, accept))
        return checksum if url.endswith("/3") else payload
    monkeypatch.setattr(feed, "_download", download)
    assert feed.download_release(candidate) == payload
    assert requests == [(feed.API + "/releases/assets/3", "application/octet-stream"),
                        (feed.API + "/releases/assets/2", "application/octet-stream")]


@pytest.mark.parametrize("change", ["wrong_name", "wrong_checksum", "wrong_digest", "wrong_size", "wrong_url", "duplicate"])
def test_asset_replacement_or_wrong_checksum_refused(monkeypatch, change):
    payload = b"checked archive"
    release, checksum = published_release(payload)
    fake_feed(monkeypatch, release=release)
    candidate = feed.checked_release()
    if change == "wrong_name":
        checksum = checksum.replace(feed.ARCHIVE_NAME.encode(), b"telegram-mcp-macos.zip")
        candidate["checksum"]["size"] = len(checksum)
        candidate["checksum"]["digest"] = None
    elif change == "wrong_checksum":
        checksum = b"0" * 64 + b"  " + feed.ARCHIVE_NAME.encode() + b"\n"
        candidate["checksum"]["digest"] = None
    elif change == "wrong_digest":
        candidate["archive"]["digest"] = "sha256:" + "0" * 64
    elif change == "wrong_size":
        candidate["archive"]["size"] += 1
    elif change == "wrong_url":
        candidate["archive"]["url"] = "https://evil.example/file"
    else:
        candidate["checksum"] = candidate["archive"].copy()
    monkeypatch.setattr(feed, "_download", lambda url, **_: checksum if url.endswith("/3") else payload)
    with pytest.raises(RuntimeError):
        feed.download_release(candidate)


@pytest.mark.parametrize("url", ["http://github.com/file", "https://evil.example/file",
                                 "https://github.com:444/file", "https://user@github.com/file"])
def test_unsafe_urls_and_intermediate_redirects_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr(feed, "build_opener", lambda *_: pytest.fail("Must reject before opening network"))
    with pytest.raises(RuntimeError, match="HTTPS GitHub"):
        feed._download(url, limit=10)
    with pytest.raises(RuntimeError, match="HTTPS GitHub"):
        feed._CheckedRedirects().redirect_request(Request("https://github.com/a"), None, 302, "", {}, url)


def test_full_release_manifest_and_generated_assets_pass():
    files = feed.validate_release_archive(archive_bytes(), VERSION)
    assert feed.DERIVED_FILES.isdisjoint(files)
    assert feed.MANIFEST_NAME not in files
    assert "src/telegram_search_mcp/windows_install.py" in files


@pytest.mark.parametrize("name", ["../bad.py", "scripts/CON.py", "scripts/file.py:stream", "scripts/file.py ",
                                  "src/telegram_search_mcp/profiles/secrets.py", "bad.exe"])
def test_archive_unsafe_paths_are_rejected(name):
    with pytest.raises(RuntimeError):
        feed.validate_release_archive(archive_bytes(add_entries=[(name, b"bad")]), VERSION)


def test_archive_case_collision_is_rejected_on_every_platform():
    with pytest.raises(RuntimeError, match="duplicate"):
        feed.validate_release_archive(archive_bytes(add_entries=[("readme.md", b"different")]), VERSION)


def test_archive_special_files_and_writable_modes_rejected():
    def alter(info):
        if info.filename.endswith("README.md"):
            info.external_attr = (stat.S_IFLNK | 0o644) << 16
    with pytest.raises(RuntimeError, match="file type or mode"):
        feed.validate_release_archive(archive_bytes(member_changes=alter), VERSION)


def test_recomputed_manifest_cannot_hide_generated_windows_asset_edits():
    files = release_files()
    files["install-windows.ps1"] = b"malicious replacement\n"
    with pytest.raises(RuntimeError, match="Generated Windows assets"):
        feed.validate_release_archive(archive_bytes(files), VERSION)


def test_windows_assets_python_is_never_executed():
    files = release_files()
    files[feed.WINDOWS_ASSETS_SOURCE] += b'raise RuntimeError("Must never execute candidate code")\n'
    with pytest.raises(RuntimeError, match="reviewed Windows assets"):
        feed.validate_release_archive(archive_bytes(files), VERSION)


def test_archive_version_must_match_published_tag():
    with pytest.raises(RuntimeError, match="checked tag"):
        feed.validate_release_archive(archive_bytes(), "0.11.1")


def test_release_revision_marker_is_manifest_bound_and_not_copied_to_git_source():
    files = release_files()
    files[feed.REVISION_FILE] = (REVISION + "\n").encode()
    source = feed.validate_release_archive(archive_bytes(files), VERSION, revision=REVISION)
    assert feed.REVISION_FILE not in source


@pytest.mark.parametrize("marker", [None, PREVIOUS + "\n", REVISION])
def test_release_revision_marker_must_match_checked_commit_exactly(marker):
    files = release_files()
    if marker is not None:
        files[feed.REVISION_FILE] = marker.encode()
    with pytest.raises(RuntimeError, match="checked commit"):
        feed.validate_release_archive(archive_bytes(files), VERSION, revision=REVISION)


def test_windows_case_insensitive_private_state_names_refused():
    files = release_files()
    files["src/telegram_search_mcp/Profiles/secret.py"] = b"private state"
    with pytest.raises(RuntimeError, match="Unsafe"):
        feed.validate_release_archive(archive_bytes(files), VERSION)


def test_overlapping_archive_file_and_directory_refused_before_extraction():
    files = release_files()
    files["scripts/helper.py"] = b"file"
    files["scripts/helper.py/test.py"] = b"file-directory conflict"
    with pytest.raises(RuntimeError, match="overlapping"):
        feed.validate_release_archive(archive_bytes(files), VERSION)


def test_feed_inventory_matches_actual_unified_release_builder():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("checked_release_builder", root / "scripts/release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    source = release.inventory(root)
    payload = release.archive_payload(source, revision=REVISION)
    version = release.manifest_for(payload)["version"]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**payload, feed.MANIFEST_NAME: release.manifest_bytes(payload)}.items():
            entry = zipfile.ZipInfo(feed.ARCHIVE_ROOT + "/" + name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | release.file_mode(name)) << 16
            archive.writestr(entry, data)
    assert feed.validate_release_archive(output.getvalue(), version, revision=REVISION) == source
