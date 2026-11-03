"""Pinned native parser checks in an uninitialized, disposable client.

Only synthetic requests are logged. No setTdlibParameters, authorization,
database or network setup is performed, so the requests cannot reach Telegram.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import uuid

import pytest

from telegram_search_mcp.native_runtime import bundled_candidates, COMMIT, VERSION
from telegram_search_mcp.pinning import MessagePins


_DRIVER = r'''
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, sys.argv[1])
from telegram_search_mcp.native_runtime import COMMIT, VERSION, verify
from telegram_search_mcp.tdjson import CtypesTdJsonTransport

data = json.load(sys.stdin)
verify(Path(data['library']))
transport = CtypesTdJsonTransport(data['library'], log_verbosity=0)
assert transport.execute({'@type': 'getOption', 'name': 'version'})['value'] == VERSION
assert transport.execute({'@type': 'getOption', 'name': 'commit_hash'})['value'] == COMMIT
transport.execute({'@type': 'setLogStream', 'log_stream': {
    '@type': 'logStreamFile', 'path': data['log'], 'max_file_size': 1000000,
    'redirect_stderr': False,
}})
transport.execute({'@type': 'setLogVerbosityLevel', 'new_verbosity_level': 3})
try:
    for number, request in enumerate(data['requests']):
        assert request['@type'] in {'pinChatMessage', 'unpinChatMessage', 'getMe',
                                    'getChatPinnedMessage', 'getMessageProperties'}
        request['@extra'] = number
        transport.send(request)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = transport.receive(0.1)
            if response and response.get('@extra') == number:
                assert response['@type'] == 'error', response
                assert response['code'] == 400, response
                assert 'setTdlibParameters' in response['message'], response
                break
        else:
            raise AssertionError('Native parser response timed out')
finally:
    transport.send({'@type': 'close'})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = transport.receive(0.1)
        if response and response.get('authorization_state', {}).get('@type') == 'authorizationStateClosed':
            break
    transport.execute({'@type': 'setLogVerbosityLevel', 'new_verbosity_level': 0})
print(json.dumps({'version': VERSION, 'commit': COMMIT, 'requests_parsed': len(data['requests'])}))
'''


class CaptureSession:
    user_id = 123

    def __init__(self, *, pinned=False):
        self.pinned = pinned

    def get_chat(self, chat_id):
        return {'@type': 'chat', 'id': chat_id, 'title': 'Synthetic recipient',
                'type': {'@type': 'chatTypeSupergroup'}}

    def request(self, payload, timeout=None):
        if payload['@type'] == 'getMessage':
            return {'@type': 'message', 'chat_id': payload['chat_id'], 'id': payload['message_id'],
                    'is_pinned': self.pinned}
        if payload['@type'] == 'getMessageProperties':
            return {'@type': 'messageProperties', 'can_be_pinned': True}
        assert payload['@type'] in {'pinChatMessage', 'unpinChatMessage'}
        self.payload = payload
        raise TimeoutError('Capture only; no network transport')


def test_pinned_native_parser_retains_exact_pin_unpin_fields(tmp_path):
    if platform.system() == 'Darwin' and platform.machine() == 'x86_64':
        pytest.skip('Intel runtime is compiled by the installer')
    libraries = bundled_candidates()
    assert libraries
    directory = tmp_path.resolve()
    # Chat IDs are native int53 values; message IDs are native int64 and must
    # survive JSON deserialization even beyond JavaScript's exact integer range.
    chat_id = -5388717433
    message_id = (2**42 + 17) << 20
    requests = []
    for disable_notification in (True, False):
        session = CaptureSession()
        pins = MessagePins(directory / 'pins', session.user_id)
        assert pins.pin(session, operation_id=uuid.uuid4().hex, chat_id=chat_id, message_id=message_id,
                        disable_notification=disable_notification)['status'] == 'unknown'
        requests.append(session.payload)
    session = CaptureSession(pinned=True)
    assert pins.unpin(session, operation_id=uuid.uuid4().hex, chat_id=chat_id,
                      message_id=message_id)['status'] == 'unknown'
    requests.extend([session.payload, {'@type': 'getMe'},
        {'@type': 'getChatPinnedMessage', 'chat_id': chat_id},
        {'@type': 'getMessageProperties', 'chat_id': chat_id, 'message_id': message_id}])
    log = directory / 'synthetic-native.log'
    result = subprocess.run(
        [sys.executable, '-I', '-c', _DRIVER, str(Path(__file__).resolve().parents[1] / 'src')],
        input=json.dumps({'library': str(libraries[0]), 'log': str(log), 'requests': requests}),
        text=True, capture_output=True, timeout=40, check=True,
    )
    assert json.loads(result.stdout) == {'version': VERSION, 'commit': COMMIT, 'requests_parsed': 6}
    logs = log.read_text(encoding='utf-8')
    parsed = re.findall(r'Receive request \d+: pinChatMessage \{(.*?)\n\}', logs, re.S)
    assert len(parsed) == 2
    for payload, notification in zip(parsed, ('true', 'false')):
        assert f'chat_id = {chat_id}' in payload
        assert f'message_id = {message_id}' in payload
        assert f'disable_notification = {notification}' in payload
        assert 'only_for_self = false' in payload
    unpin = re.findall(r'Receive request \d+: unpinChatMessage \{(.*?)\n\}', logs, re.S)
    assert len(unpin) == 1
    assert f'chat_id = {chat_id}' in unpin[0]
    assert f'message_id = {message_id}' in unpin[0]
    assert 'unpinAll' not in logs
    assert 'getChatPinnedMessage' in logs and 'getMessageProperties' in logs and 'getMe' in logs
