#!/usr/bin/env python3
"""Verify this run's artifacts and publish a new version after successful main CI.

Without --publish, only local verification and release-note preparation run.
Already published releases are never rewritten. Incomplete drafts may be resumed
only for the same exact commit, with all asset bytes checked before publication.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

REPOSITORY = 'prabchevski/telegram-mcp'
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release_builder', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def current_notes(source: Path, version: str) -> str:
    text = (source / 'CHANGELOG.md').read_text()
    sections = re.split(r'^## ', text, flags=re.M)[1:]
    if not sections or not sections[0].startswith(version + ' — '):
        raise RuntimeError('The first changelog entry must describe the package version')
    for name, expected in [('README.md', f'# Telegram MCP · {version}'),
                           ('VERIFICATION.md', f'## Version {version} — ')]:
        if expected not in (source / name).read_text():
            raise RuntimeError(f'{name} does not describe the current version')
    changes = sections[0].split('\n', 1)[1].strip()
    return f'''Local Telegram MCP for **Codex on macOS and Windows x64**, **Gemini CLI on macOS**, and **ChatGPT Work on Windows x64**. Search chats and subscriptions, read history, download media, transcribe voice messages, and optionally prepare, send and edit your own messages. 15 tools by default, 22 with sending and own-message editing enabled.

Public channel search is optional: first search account history, then check the account's actual free quota, explain the cost and wait for explicit user confirmation. Paid Stars searches are disabled in code.

## Changes in {version}

{changes}

## Install or upgrade

Give your local agent this instruction:

> Install Telegram MCP from https://github.com/{REPOSITORY}. Read INSTALL.md, detect my operating system and client, install the latest verified release and check the connection. I will enter Telegram credentials in the local login window.

| Platform and client | Download | Instructions |
| --- | --- | --- |
| macOS: Codex or Gemini CLI | [telegram-mcp-macos.zip](https://github.com/{REPOSITORY}/releases/download/v{version}/telegram-mcp-macos.zip) · [SHA-256](https://github.com/{REPOSITORY}/releases/download/v{version}/telegram-mcp-macos.zip.sha256) | [macOS setup](https://github.com/{REPOSITORY}/blob/v{version}/INSTALL_MACOS.md) |
| Windows x64: Codex desktop or ChatGPT Work | [telegram-mcp-windows.zip](https://github.com/{REPOSITORY}/releases/download/v{version}/telegram-mcp-windows.zip) · [SHA-256](https://github.com/{REPOSITORY}/releases/download/v{version}/telegram-mcp-windows.zip.sha256) | [Windows setup](https://github.com/{REPOSITORY}/blob/v{version}/INSTALL_WINDOWS.md) |

Verify the matching checksum before extracting. On Windows, run **install-windows.ps1** and enable the local Telegram MCP plugin in **Plugins → Personal** in Codex or ChatGPT Work. On macOS, open **install-macos.command** or follow the agent guide to register the requested client directly. Restart the client after installation. Compatible existing Telegram logins are preserved; every new user signs in to their own account locally.

Verified daily updates are enabled by default on both supported platforms. Windows uses a current-user daily/logon task, a private pinned uv runtime and the published Windows asset for the exact successful canonical CI SHA. An explicit opt-out, Telegram login, sending preference and owner plugin changes are preserved. Pre-0.11 Windows installations need this installer once. Use the installed entry point for `updates status`, `update --check`, `updates off` and `updates on`; restart the client to load new tool definitions. Release CI always checks Windows Server; clean Windows 10/11 installer checks run for installation, platform, dependency, packaging or CI changes, and also weekly or on demand. Real client UI activation and Telegram account login still require acceptance on the user's computer; see the verification guide for coverage limits. Native Windows ARM64 is not supported.

[Installation guide](https://github.com/{REPOSITORY}/blob/v{version}/INSTALL.md) · [Verification and limits](https://github.com/{REPOSITORY}/blob/v{version}/VERIFICATION.md)

Managed 0.6.1 and 0.7 installations with automatic updates enabled migrate standard tool registrations automatically. Pre-rename 0.6.0 and 0.7.0 installations stranded with old tool lists need the installer once. Removed/customized registrations are preserved. Intel preparation is implemented but has not been tested on physical Intel hardware. Gemini web/mobile are not supported.

Assets contain source code and package metadata, never Telegram credentials, sessions or chat history.
'''


def prepare(source: Path, directory: Path) -> tuple[str, list[Path], Path]:
    version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise RuntimeError('Invalid release version')
    notes = current_notes(source, version)
    archive = directory / f'telegram-mcp-macos-v{version}.zip'
    manifest = release.verify_archive(archive)
    if manifest != release.manifest_for(release.archive_payload(release.inventory(source), revision=release.source_revision(source))):
        raise RuntimeError('Archive does not match this checked commit')
    wheel = directory / f'telegram_search_mcp-{version}-py3-none-any.whl'
    release.verify_wheel(wheel, source)
    checksum = archive.with_suffix('.zip.sha256')
    expected = f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n'
    if checksum.read_text() != expected:
        raise RuntimeError('Archive checksum does not match')
    inventory = archive.with_suffix('.manifest.json')
    if json.loads(inventory.read_text()) != manifest:
        raise RuntimeError('External file inventory does not match')
    stable = directory / 'telegram-mcp-macos.zip'
    shutil.copyfile(archive, stable)
    stable_sum = stable.with_suffix('.zip.sha256')
    stable_sum.write_text(f'{hashlib.sha256(stable.read_bytes()).hexdigest()}  {stable.name}\n')
    windows = directory / 'telegram-mcp-windows.zip'
    shutil.copyfile(archive, windows)
    windows_sum = windows.with_suffix('.zip.sha256')
    windows_sum.write_text(f'{hashlib.sha256(windows.read_bytes()).hexdigest()}  {windows.name}\n')
    body = directory / 'release-notes.md'
    body.write_text(notes)
    return version, [archive, checksum, inventory, wheel, stable, stable_sum, windows, windows_sum], body


def api(path: str) -> dict | None:
    result = subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/{path}'], capture_output=True, text=True)
    if result.returncode:
        if '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError('GitHub API request failed; release was not published')
    return json.loads(result.stdout)


def release_info(tag: str) -> dict | None:
    # REST releases/tags only resolves published tags. gh release view also
    # finds drafts whose tag is created only when the release is published.
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPOSITORY,
                             '--json', 'isDraft,targetCommitish,assets'], capture_output=True, text=True)
    if result.returncode:
        if result.stderr.strip() == 'release not found':
            return None
        raise RuntimeError('Release lookup failed; publication was not attempted')
    value = json.loads(result.stdout)
    return {'draft': value['isDraft'], 'target_commitish': value['targetCommitish'], 'assets': value['assets']}


def publish(version: str, assets: list[Path], notes: Path, sha: str) -> None:
    tag = 'v' + version
    existing = release_info(tag)
    if existing and not existing['draft']:
        print(f'{tag} is already published; its tag and assets remain unchanged.')
        return
    if existing and existing['target_commitish'] != sha:
        raise RuntimeError('Existing draft belongs to a different commit')
    ref = api('git/ref/tags/' + tag)
    if ref and (ref['object']['type'] != 'commit' or ref['object']['sha'] != sha):
        raise RuntimeError('Existing tag does not point at this exact commit')
    if not existing:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', sha,
                        '--draft', '--title', 'Telegram MCP ' + version,
                        '--notes-file', str(notes)], check=True)
    else:
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY,
                        '--title', 'Telegram MCP ' + version, '--notes-file', str(notes)], check=True)
    subprocess.run(['gh', 'release', 'upload', tag, '--repo', REPOSITORY, '--clobber',
                    *map(str, assets)], check=True)
    uploaded = release_info(tag)
    expected = {path.name: 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    actual = {item['name']: item.get('digest') for item in uploaded['assets']}
    if actual != expected:
        raise RuntimeError('Uploaded asset digests differ; release remains a draft')
    subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest'], check=True)
    print('Published https://github.com/' + REPOSITORY + '/releases/tag/' + tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    version, assets, notes = prepare(ROOT, args.directory.resolve())
    if not args.publish:
        print(f'Verified v{version}: {len(assets)} assets and release notes; no publication requested.')
        return
    sha = os.environ.get('GITHUB_SHA', '')
    if (os.environ.get('GITHUB_EVENT_NAME') != 'push' or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or not re.fullmatch(r'[0-9a-f]{40}', sha)
            or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() != sha):
        raise RuntimeError('Publication requires the checked canonical main CI commit')
    publish(version, assets, notes, sha)


if __name__ == '__main__':
    main()
