"""Exercise the installed native parser without opening a profile or connecting."""
from pathlib import Path
import platform
import pytest
from telegram_search_mcp.native_runtime import bundled_candidates, verify, VERSION, COMMIT
from telegram_search_mcp.tdjson import CtypesTdJsonTransport


def test_pinned_native_library_version_and_recognition_parser():
    candidates = bundled_candidates()
    if platform.system() == 'Darwin' and platform.machine() == 'x86_64':
        pytest.skip('Intel runtime is compiled by the installer')
    assert candidates
    verify(candidates[0])
    transport = CtypesTdJsonTransport(candidates[0], log_verbosity=0)
    assert transport.execute({'@type':'getOption','name':'version'})['value']==VERSION
    assert transport.execute({'@type':'getOption','name':'commit_hash'})['value']==COMMIT
    result=transport.execute({'@type':'recognizeSpeech','chat_id':123,'message_id':1048576})
    assert result['@type']=='error'
    assert 'synchronously' in result['message']


def test_runtime_rejects_library_outside_pinned_build(monkeypatch,tmp_path):
    from telegram_search_mcp import native_runtime
    class Function:
        def __call__(self,request):
            return b'{"@type":"optionValueString","value":"1.8.0"}'
    class Library:
        td_execute=Function()
    monkeypatch.setattr(native_runtime.ctypes,'CDLL',lambda _:Library())
    with pytest.raises(RuntimeError,match='pinned'):
        verify(tmp_path/'wrong-library')


@pytest.mark.parametrize('cached', [False, True])
def test_old_intel_preflight_keeps_old_version_until_cache_is_ready(monkeypatch, tmp_path, cached):
    from telegram_search_mcp import native_runtime as runtime
    root = tmp_path / 'application'
    version = root / 'staged'
    module = version / 'src/telegram_search_mcp/native_runtime.py'
    module.parent.mkdir(parents=True)
    (root / '.telegram-search-install-root').write_text('telegram-search-mcp\n')
    (root / 'current').symlink_to('old')
    monkeypatch.setattr(runtime, '__file__', str(module))
    monkeypatch.setattr(runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'x86_64')
    calls = []
    monkeypatch.setattr(runtime, 'prepare', lambda path: calls.append(('copy-verified-cache', path)))
    monkeypatch.setattr(runtime, 'start_background_prepare', lambda path: calls.append(('background-build', path)))
    if cached:
        library = root / 'native' / runtime.COMMIT / 'libtdjson.dylib'
        library.parent.mkdir(parents=True)
        library.touch()
        runtime.check_staged_runtime()
        assert calls == [('copy-verified-cache', version)]
    else:
        with pytest.raises(RuntimeError, match='previous installation remains active'):
            runtime.check_staged_runtime()
        assert calls == [('background-build', root)]
    assert (root / 'current').readlink() == Path('old')


def test_background_intel_helper_survives_deleted_staging_and_uses_old_interpreter(monkeypatch, tmp_path):
    from telegram_search_mcp import native_runtime as runtime, installation, launchers
    root = tmp_path.resolve() / 'application'
    installation.prepare_root(root)
    old = root / 'old'
    old.mkdir()
    (old / launchers.VERSION_MARKER).write_text('telegram-search-mcp\n')
    (root / 'current').symlink_to(old.name)
    calls = []
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    runtime.start_background_prepare(root)
    args, options = calls[0]
    assert args[0] == str(old / '.venv/bin/python')
    helper = Path(args[2])
    assert helper.parent == root / 'native'
    assert helper.read_bytes() == Path(runtime.__file__).read_bytes()
    assert options['start_new_session'] is True
    assert args[-2:] == ['--cache-only', str(root)]


def test_background_native_build_is_serialized(monkeypatch, tmp_path):
    import fcntl
    import os
    from telegram_search_mcp import native_runtime as runtime, installation
    from telegram_search_mcp.service import open_private_file
    root = tmp_path.resolve() / 'application'
    installation.prepare_root(root)
    (root / 'native').mkdir()
    descriptor = open_private_file(root / 'native/prepare.lock')
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        monkeypatch.setattr(runtime, '_build_cache', lambda _: pytest.fail('second build'))
        monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: pytest.fail('second brew'))
        assert runtime.prepare_cache(root, background=True) is None
    finally:
        os.close(descriptor)
