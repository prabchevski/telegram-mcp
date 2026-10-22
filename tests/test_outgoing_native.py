"""Check document serialization with the pinned TDLib parser, without a profile.

Only synthetic requests reach an uninitialized client in a disposable subprocess.
No setTdlibParameters, authorization, database, or network setup is performed.
The request log is deliberately confined to that process and synthetic content.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import uuid

import pytest

from telegram_search_mcp.native_runtime import bundled_candidates, COMMIT, VERSION
from telegram_search_mcp.outgoing import Outbox
from telegram_search_mcp.platform_support import open_private_file


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
        assert request['@type'] == 'sendMessage'
        # An uninitialized native client has no account or connection. These
        # requests exercise JSON deserialization only; dispatch cannot proceed.
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


class _CaptureSession:
    user_id = 123

    def get_chat(self, chat_id):
        return {'@type': 'chat', 'id': chat_id, 'title': 'Synthetic recipient',
                'type': {'@type': 'chatTypePrivate'}}

    def request(self, payload, timeout=None):
        if payload['@type'] == 'getMessage':
            return {'@type': 'message', 'chat_id': payload['chat_id'], 'id': payload['message_id']}
        if payload['@type'] == 'getMessageProperties':
            return {'@type': 'messageProperties', 'can_be_replied': True}
        assert payload['@type'] == 'sendMessage'
        self.payload = payload
        raise TimeoutError('Capture only; no network transport')


@pytest.mark.parametrize('reply_id', [None, 1048576])
def test_pinned_parser_keeps_outbox_document_file_and_caption(tmp_path, reply_id):
    if platform.system() == 'Darwin' and platform.machine() == 'x86_64':
        pytest.skip('Intel runtime is compiled by the installer')
    libraries = bundled_candidates()
    assert libraries
    directory = tmp_path.resolve()
    attachment = directory / 'synthetic.md'
    with os.fdopen(open_private_file(attachment, exclusive=True), 'wb') as stream:
        stream.write(b'x' * 29054)
    caption = 'x' * 600 + '\U0001f44b'  # 602 UTF-16 code units
    session = _CaptureSession()
    box = Outbox(directory / 'outbox', session.user_id)
    draft_id = uuid.uuid4().hex
    box.prepare(session, draft_id=draft_id, recipient='42', text=caption,
                file_path=str(attachment), reply_to_message_id=reply_id)
    assert box.send(session, draft_id=draft_id)['status'] == 'unknown'

    # Contrast the obsolete shape with the actual production request builder.
    # TDLib accepts a concrete object's JSON regardless of its @type: the old
    # shape becomes inputDocument(document=null), silently dropping the path.
    old = copy.deepcopy(session.payload)
    old['input_message_content'] = {
        '@type': 'inputMessageDocument',
        'document': {'@type': 'inputFileLocal', 'path': str(attachment)},
        'thumbnail': None, 'disable_content_type_detection': True,
        'caption': {'@type': 'formattedText', 'text': caption, 'entities': []},
    }
    log = directory / 'synthetic-native.log'
    result = subprocess.run(
        [sys.executable, '-I', '-c', _DRIVER, str(Path(__file__).resolve().parents[1] / 'src')],
        input=json.dumps({'library': str(libraries[0]), 'log': str(log),
                          'requests': [old, session.payload]}),
        text=True, capture_output=True, timeout=20, check=True,
    )
    assert json.loads(result.stdout) == {'version': VERSION, 'commit': COMMIT, 'requests_parsed': 2}
    parsed = re.findall(r'Receive request \d+: sendMessage \{(.*?)\n\}', log.read_text(encoding='utf-8'), re.S)
    assert len(parsed) == 2
    assert re.search(r'document = inputDocument \{\s+document = null', parsed[0])
    assert re.search(r'document = inputDocument \{\s+document = inputFileLocal \{', parsed[1])
    assert 'disable_content_type_detection = true' in parsed[1]
    assert 'x' * 600 in parsed[1]
    if reply_id is not None:
        assert 'message_id = 1048576' in parsed[1]
    else:
        assert 'reply_to = null' in parsed[1]
