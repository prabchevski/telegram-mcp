"""Release publication must retain old releases and verify every uploaded byte."""
import hashlib
import importlib.util
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publication', ROOT / 'scripts/publish-release.py')
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)
SHA = 'a' * 40


@pytest.fixture
def assets(tmp_path):
    asset = tmp_path / 'telegram-mcp-macos.zip'
    asset.write_bytes(b'verified archive')
    notes = tmp_path / 'release-notes.md'
    notes.write_text('Reviewed release notes')
    return [asset], notes


def test_published_release_is_never_overwritten(monkeypatch, assets):
    monkeypatch.setattr(publication, 'release_info', lambda _: {'draft': False})
    monkeypatch.setattr(publication.subprocess, 'run', lambda *a, **k: pytest.fail('must not mutate published release'))
    publication.publish('0.7.1', *assets, SHA)


@pytest.mark.parametrize('bad_digest', [False, True])
def test_publish_only_after_all_uploaded_digests_match(monkeypatch, assets, bad_digest):
    paths, notes = assets
    digest = 'sha256:' + hashlib.sha256(paths[0].read_bytes()).hexdigest()
    responses = iter([None, None, {'assets': [{'name': paths[0].name, 'digest': 'wrong' if bad_digest else digest}]}])
    monkeypatch.setattr(publication, 'api', lambda _: next(responses))
    monkeypatch.setattr(publication, 'release_info', lambda _: next(responses))
    calls = []
    monkeypatch.setattr(publication.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    if bad_digest:
        with pytest.raises(RuntimeError, match='remains a draft'):
            publication.publish('0.7.1', paths, notes, SHA)
    else:
        publication.publish('0.7.1', paths, notes, SHA)
    assert '--draft' in calls[0] and calls[0][calls[0].index('--target') + 1] == SHA
    assert ['gh', 'release', 'upload'] == calls[1][:3]
    assert any('--draft=false' in call for call in calls) is not bad_digest


@pytest.mark.parametrize('conflict', ['draft', 'tag'])
def test_release_at_another_commit_is_not_replaced(monkeypatch, assets, conflict):
    responses = iter([{'draft': True, 'target_commitish': 'b' * 40}] if conflict == 'draft' else [None, {'object': {'type': 'commit', 'sha': 'b' * 40}}])
    monkeypatch.setattr(publication, 'api', lambda _: next(responses))
    monkeypatch.setattr(publication, 'release_info', lambda _: next(responses))
    monkeypatch.setattr(publication.subprocess, 'run', lambda *a, **k: pytest.fail('must not mutate different commit'))
    with pytest.raises(RuntimeError, match='different commit|exact commit'):
        publication.publish('0.7.1', *assets, SHA)


@pytest.mark.parametrize('filename', ['CHANGELOG.md', 'README.md', 'VERIFICATION.md'])
def test_outdated_release_documents_block_publication(tmp_path, filename):
    files = {'CHANGELOG.md': '# Changelog\n\n## 0.7.1 — 2026-09-16\n\n- Current changes\n',
             'README.md': '# Telegram MCP · 0.7.1\n', 'VERIFICATION.md': '## Version 0.7.1 — September 16, 2026\n'}
    for name, text in files.items():
        (tmp_path / name).write_text(text.replace('0.7.1', '0.4.0') if name == filename else text)
    with pytest.raises(RuntimeError, match='version'):
        publication.current_notes(tmp_path, '0.7.1')


def test_current_notes_include_upgrade_exceptions():
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    notes = publication.current_notes(ROOT, version)
    assert 'Pre-rename 0.6.0' in notes and '0.7.0 installations stranded' in notes
    assert 'physical Intel' in notes


def test_draft_lookup_uses_cli_before_github_creates_tag(monkeypatch):
    def run(args, **kwargs):
        assert args[:3] == ['gh', 'release', 'view']
        return subprocess.CompletedProcess(args, 0, json.dumps({'isDraft': True, 'targetCommitish': SHA, 'assets': []}), '')
    monkeypatch.setattr(publication.subprocess, 'run', run)
    assert publication.release_info('v0.7.1') == {'draft': True, 'target_commitish': SHA, 'assets': []}


@pytest.mark.parametrize('error,absent', [('release not found\n', True), ('authentication failed\n', False)])
def test_release_lookup_distinguishes_absence_from_request_failure(monkeypatch, error, absent):
    monkeypatch.setattr(publication.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 1, '', error))
    if absent:
        assert publication.release_info('v0.7.1') is None
    else:
        with pytest.raises(RuntimeError, match='lookup failed'):
            publication.release_info('v0.7.1')
