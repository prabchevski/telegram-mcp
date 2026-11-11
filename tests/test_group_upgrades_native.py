"""Pinned TDLib constructor parsing without parameters, login or network setup."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import subprocess
import sys

import pytest

from telegram_search_mcp.native_runtime import bundled_candidates, COMMIT, VERSION


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
        assert request['@type'] in {'upgradeBasicGroupChatToSupergroupChat', 'getBasicGroup',
            'getSupergroupFullInfo', 'createSupergroupChat'}
        # td_execute parses the constructor but cannot execute these async RPCs.
        parsed = transport.execute(request)
        assert parsed['@type'] == 'error' and parsed['code'] == 400, parsed
        assert "can't be executed synchronously" in parsed['message'], parsed
        request['@extra'] = number
        transport.send(request)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = transport.receive(0.1)
            if response and response.get('@extra') == number:
                assert response['@type'] == 'error' and response['code'] == 400, response
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


def test_pinned_native_group_upgrade_and_reconciliation_constructors(tmp_path):
    if platform.system() == 'Darwin' and platform.machine() == 'x86_64':
        pytest.skip('Intel runtime is compiled by the installer')
    libraries = bundled_candidates()
    assert libraries
    chat_id, basic_id, supergroup_id = -5388717433, 5388717433, 88717433
    requests = [
        {'@type': 'upgradeBasicGroupChatToSupergroupChat', 'chat_id': chat_id},
        {'@type': 'getBasicGroup', 'basic_group_id': basic_id},
        {'@type': 'getSupergroupFullInfo', 'supergroup_id': supergroup_id},
        {'@type': 'createSupergroupChat', 'supergroup_id': supergroup_id, 'force': False},
    ]
    log = tmp_path.resolve() / 'synthetic-group-upgrade-native.log'
    result = subprocess.run(
        [sys.executable, '-I', '-c', _DRIVER, str(Path(__file__).resolve().parents[1] / 'src')],
        input=json.dumps({'library': str(libraries[0]), 'log': str(log), 'requests': requests}),
        text=True, capture_output=True, timeout=40, check=True,
    )
    assert json.loads(result.stdout) == {'version': VERSION, 'commit': COMMIT, 'requests_parsed': 4}
    logs = log.read_text(encoding='utf-8')
    upgrade = re.findall(r'Receive request \d+: upgradeBasicGroupChatToSupergroupChat \{(.*?)\n\}', logs, re.S)
    assert len(upgrade) == 1 and f'chat_id = {chat_id}' in upgrade[0]
    assert f'basic_group_id = {basic_id}' in logs
    assert f'supergroup_id = {supergroup_id}' in logs
    assert 'force = false' in logs
    assert 'createNew' not in logs and 'setTdlibParameters {' not in logs
