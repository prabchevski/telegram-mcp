"""Published archive provenance survives installation without changing Git snapshots."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
from test_packaging import source, release

SHA = 'a' * 40


def test_revision_marker_is_hashed_in_release_and_excluded_from_raw_git_payload(source, tmp_path):
    (source / 'SOURCE_REVISION').write_text(SHA + '\n')
    raw = release.inventory(source)
    assert 'SOURCE_REVISION' not in raw
    archive_path = release.build(source, tmp_path / 'dist')
    manifest = release.verify_archive(archive_path)
    record = next(item for item in manifest['files'] if item['path'] == 'SOURCE_REVISION')
    assert record['sha256'] == hashlib.sha256((SHA + '\n').encode()).hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        files = {item.filename.split('/', 1)[1]: archive.read(item) for item in archive.infolist()}
        assert files.pop(release.MANIFEST_NAME)
        assert files['SOURCE_REVISION'] == (SHA + '\n').encode()
        assert release.source_payload(files) == raw
        archive.extractall(tmp_path / 'unpacked')
    extracted = tmp_path / 'unpacked' / release.PACKAGE_ROOT
    assert release.inventory(extracted) == raw
    assert release.source_revision(extracted) == SHA


@pytest.mark.parametrize('bad', ['A' * 40, 'a' * 39, 'a' * 41, 'revision', ''])
def test_malformed_source_revision_is_rejected_before_packaging(source, tmp_path, bad):
    (source / 'SOURCE_REVISION').write_text(bad + '\n')
    with pytest.raises(release.ReleaseError, match='revision'):
        release.build(source, tmp_path / 'dist')


def test_revision_mutation_without_rehashing_manifest_is_rejected(source, tmp_path):
    (source / 'SOURCE_REVISION').write_text(SHA + '\n')
    original = release.build(source, tmp_path / 'dist')
    with zipfile.ZipFile(original) as archive:
        files = {item.filename.split('/', 1)[1]: archive.read(item) for item in archive.infolist()}
    files['SOURCE_REVISION'] = ('b' * 40 + '\n').encode()
    tampered = tmp_path / 'tampered.zip'
    release.write_zip(tampered, files)
    with pytest.raises(release.ReleaseError, match='manifest'):
        release.verify_archive(tampered)


def test_legacy_codeload_reconstruction_never_includes_generated_revision(source, tmp_path):
    (source / 'SOURCE_REVISION').write_text(SHA + '\n')
    archive = release.build(source, tmp_path / 'dist')
    script = Path(__file__).resolve().parents[1] / 'scripts/smoke-upgrade-06.py'
    spec = importlib.util.spec_from_file_location('legacy_snapshot_revision_test', script)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    import io
    payload = legacy.codeload_snapshot(archive, release)
    with zipfile.ZipFile(io.BytesIO(payload)) as raw:
        names = {item.filename.split('/', 1)[1] for item in raw.infolist()}
    assert 'SOURCE_REVISION' not in names
    assert release.MANIFEST_NAME not in names
    assert not set(release.DERIVED_FILES) & names
