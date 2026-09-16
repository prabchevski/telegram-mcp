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
