#!/usr/bin/env python3
"""Exercise the unmodified 0.6.1 updater against this release in disposable installs.

Only GitHub responses and desktop notifications are substituted. Both installers,
Python environments, client files, activation and MCP discovery are real. No
Telegram session, Keychain, user client settings or LaunchAgents are accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import zipfile
from urllib.request import urlopen

LEGACY = 'c5a67ff349248ac0960b39b2e6fae6be9a207bb3'
LEGACY_SHA256 = 'd8582bee9d3800a2199b2ab8fa7be7a68407830f9ce8ae811461687e739d321b'
CANDIDATE = 'a' * 40

OLD_UPDATE = '''
import json, sys
from pathlib import Path
from telegram_search_mcp import __version__, updater
assert __version__ == '0.6.1'
root, payload = Path(sys.argv[1]), Path(sys.argv[2]).read_bytes()
receipt = json.loads((root / 'installation.json').read_bytes())
receipt['auto_update'] = True  # Simulate an existing schedule; never create one.
(root / 'installation.json').write_text(json.dumps(receipt))
paths = {client: Path(path) for client, path in receipt['clients'].items()}
before = {client: path.read_bytes() for client, path in paths.items()}
updater.checked_revision = lambda: 'a' * 40
updater._json = lambda _: {'status': 'ahead'}
updater._download = lambda *args, **kwargs: payload
updater.find_uv = lambda: sys.argv[3]
assert updater.update(root, scheduled=True)['status'] == 'updated'
assert all(path.read_bytes() == before[client] for client, path in paths.items())
print('PASS: actual 0.6.1 updater installed new code and retained its old allowlists.')
'''

NEW_ACTIVATION = '''
import json, sys, tomllib
from pathlib import Path
from telegram_search_mcp import activation, updater
root = Path(sys.argv[1])
notifications = []
activation.notify_restart = lambda: notifications.append(True) or True
updater.checked_revision = lambda: (_ for _ in ()).throw(AssertionError('daily check is not due'))
assert updater.update(root, scheduled=True)['status'] == 'not_due'
receipt = json.loads((root / 'installation.json').read_bytes())
assert receipt['auto_update'] is True
for client, value in receipt['clients'].items():
    path = Path(value)
    data = tomllib.loads(path.read_text()) if client == 'codex' else json.loads(path.read_text())
    entry = data['mcp_servers']['telegram_search'] if client == 'codex' else data['mcpServers']['telegram-search']
    tools = entry['enabled_tools' if client == 'codex' else 'includeTools']
    assert {'telegram_list_voice_messages', 'telegram_transcribe_voice'} <= set(tools)
    assert len(tools) == (9 if sys.argv[2] == 'on' else 6)
    assert (data['model'] if client == 'codex' else data['ui']['theme']) == 'preserved'
assert len(notifications) == 1
assert updater.update(root, scheduled=True)['status'] == 'not_due'
assert len(notifications) == 1
print('PASS: new scheduled updater completed both registrations once, retained preferences, and requested a restart notification.')
'''


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--legacy-archive', type=Path)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('The real 0.6 upgrade smoke test currently requires Apple Silicon macOS')
    uv = shutil.which('uv')
    assert uv, 'uv is required'
    release = load_script('release')
    smoke = load_script('smoke-install-macos')
    release.verify_archive(args.archive)
    if args.legacy_archive:
        legacy_payload = args.legacy_archive.read_bytes()
    else:
        with urlopen('https://codeload.github.com/prabchevski/telegram-mcp/zip/' + LEGACY, timeout=30) as response:
            legacy_payload = response.read(20 * 1024 * 1024)
    assert hashlib.sha256(legacy_payload).hexdigest() == LEGACY_SHA256
    from telegram_search_mcp.updater import extract_source
    with tempfile.TemporaryDirectory(prefix='telegram-upgrade-06-') as directory:
        temporary = Path(directory).resolve()
        legacy_source = extract_source(legacy_payload, LEGACY, temporary / 'legacy-source')
        # Transform the verified release into the same file layout as codeload.
        payload = io.BytesIO()
        with zipfile.ZipFile(args.archive) as archive, zipfile.ZipFile(payload, 'w') as target:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                name = item.filename.split('/', 1)[1]
                if name != release.MANIFEST_NAME:
                    target.writestr('telegram-mcp-' + CANDIDATE + '/' + name, archive.read(item))
        candidate = temporary / 'candidate.zip'
        candidate.write_bytes(payload.getvalue())
        for sending in ('off', 'on'):
            case = temporary / sending
            codex, gemini = case / 'codex/config.toml', case / 'gemini/settings.json'
            codex.parent.mkdir(parents=True)
            gemini.parent.mkdir(parents=True)
            codex.write_text('model = "preserved"\n')
            gemini.write_text(json.dumps({'ui': {'theme': 'preserved'}}))
            root = case / 'application'
            subprocess.run(['/bin/bash', str(legacy_source / 'install-macos.command'), '--prepare-only',
                            '--skip-system-deps', '--clients', 'both', '--install-dir', str(root),
                            '--codex-config', str(codex), '--gemini-config', str(gemini)], check=True, timeout=300)
            old = (root / 'current').resolve()
            if sending == 'on':
                subprocess.run([str(old / 'tgsearch'), 'sending', 'on'], check=True, timeout=30)
            subprocess.run([str(old / '.venv/bin/python'), '-I', '-c', OLD_UPDATE, str(root), str(candidate), uv], check=True, timeout=300)
            new = (root / 'current').resolve()
            assert old != new and (old / '.venv/bin/python').exists()
            subprocess.run([str(new / '.venv/bin/python'), '-I', '-c', NEW_ACTIVATION, str(root), sending], check=True, timeout=30)
            discovery = [str(new / '.venv/bin/python'), '-I', '-c', smoke.MCP_DISCOVERY_CHECK, str(root / 'launch-mcp.command')]
            if sending == 'on':
                discovery.append('sending')
            subprocess.run(discovery, check=True, timeout=30)
            print('PASS: 0.6.1 automatic transition with sending=' + sending)


if __name__ == '__main__':
    main()
