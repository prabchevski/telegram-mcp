from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

import pytest

from telegram_search_mcp.chat_drafts import DraftRequest, SetDraftRequest, current, result, set_draft, version
from telegram_search_mcp.downloads import DownloadRequest, download, export_file
from telegram_search_mcp.outgoing import Outbox
from telegram_search_mcp.backend import MediaError, MediaTooLargeError


class Session:
    user_id = 9

    def __init__(self, tmp_path):
        self.calls, self.draft = [], None
        self.timeout = False
        self.cache = tmp_path / 'cache'
        self.cache.mkdir()
        self.file = self.cache / 'table.xlsx'
        self.file.write_bytes(b'Excel attachment')
        self.protected = False

    def get_chat(self, chat_id, timeout=None):
        return {'@type': 'chat', 'id': chat_id, 'title': 'Recipient', 'type': {'@type': 'chatTypePrivate'},
                'draft_message': self.draft, 'has_protected_content': self.protected}

    def request(self, payload, timeout=None):
        self.calls.append(payload)
        kind = payload['@type']
        if kind == 'getMessage':
            return {'@type': 'message', 'id': payload['message_id'], 'chat_id': payload['chat_id'],
                    'content': {'@type': 'messageDocument', 'document': {'file_name': '../../table.xlsx',
                        'document': {'id': 8, 'size': self.file.stat().st_size}, 'mime_type': 'application/vnd.test'}}}
        if kind == 'getMessageProperties':
            return {'can_be_saved': not self.protected, 'can_be_replied': True}
        if kind == 'downloadFile':
            return {'local': {'is_downloading_completed': True, 'path': str(self.file)}}
        if kind == 'setChatDraftMessage':
            self.draft = payload['draft_message']
            if self.timeout:
                raise TimeoutError()
            return {'@type': 'ok'}
        if kind == 'sendMessage':
            if self.timeout:
                raise TimeoutError()
            return {'@type': 'message', 'id': 123, 'chat_id': payload['chat_id'], 'is_outgoing': True,
                    'scheduling_state': payload['options']['scheduling_state']}
        raise AssertionError(payload)


def test_download_preserves_bytes_and_cannot_select_export_path(tmp_path):
    s = Session(tmp_path)
    r = download(s, DownloadRequest(chat_id=1, message_id=2), cache=s.cache, destination=tmp_path/'downloads')
    assert Path(r['path']).read_bytes() == b'Excel attachment'
    assert Path(r['path']).parent.parent == tmp_path/'downloads'
    assert Path(r['path']).stat().st_mode & 0o777 == 0o600
    assert r['file_name'] == 'table.xlsx'
    assert [p for p in s.calls if p['@type'] == 'downloadFile'][0]['limit'] == 20 * 1024 * 1024 + 1
    repeat = download(s, DownloadRequest(chat_id=1, message_id=2), cache=s.cache, destination=tmp_path/'downloads')
    assert repeat['path'] != r['path']


def test_protected_and_oversize_downloads_never_start(tmp_path):
    s = Session(tmp_path)
    s.protected = True
    with pytest.raises(MediaError, match='does not allow'):
        download(s, DownloadRequest(chat_id=1, message_id=2), cache=s.cache, destination=tmp_path/'downloads')
    s.protected = False
    with pytest.raises(MediaTooLargeError):
        download(s, DownloadRequest(chat_id=1, message_id=2, max_bytes=1), cache=s.cache, destination=tmp_path/'downloads')
    assert not any(p['@type'] == 'downloadFile' for p in s.calls)


def test_file_export_rejects_external_path_and_symlink(tmp_path):
    s = Session(tmp_path)
    other = tmp_path/'outside'
    other.write_bytes(b'private')
    alias = s.cache/'alias'
    alias.symlink_to(s.file)
    for path in (other, alias):
        with pytest.raises(MediaError, match='unsafe'):
            export_file(path, s.cache, tmp_path/'downloads', 'anything', 100)


def test_native_draft_version_guard_and_no_duplicate_after_timeout(tmp_path):
    s = Session(tmp_path)
    params = dict(chat_id=1, operation_id=uuid.uuid4().hex, text='Review on phone', expected_version=version(None))
    s.draft = {'content': {'@type': 'draftMessageContentText', 'text': {'text': 'My own edit'}}}
    with pytest.raises(ValueError, match='draft changed'):
        set_draft(s, SetDraftRequest(**params), tmp_path/'draft-ops')
    assert not s.calls
    s.draft, s.timeout = None, True
    first = set_draft(s, SetDraftRequest(**params), tmp_path/'draft-ops')
    assert first['status'] == 'unknown'
    assert s.draft['content']['text']['text'] == params['text']
    s.draft = {'content': {'text': {'text': 'User edited later'}}}
    again = set_draft(s, SetDraftRequest(**params), tmp_path/'draft-ops')
    assert first == again
    assert len([p for p in s.calls if p['@type'] == 'setChatDraftMessage']) == 1
    assert result(DraftRequest(chat_id=1), current(s, DraftRequest(chat_id=1)))['text']['value'] == 'User edited later'


def test_native_draft_clear_is_explicit_and_account_bound(tmp_path):
    s = Session(tmp_path)
    req = SetDraftRequest(chat_id=1, operation_id=uuid.uuid4().hex, text='Hello', expected_version=version(None))
    written = set_draft(s, req, tmp_path/'draft-ops')
    assert written['status'] == 'stored'
    req2 = SetDraftRequest(chat_id=1, operation_id=uuid.uuid4().hex, text='', expected_version=version(s.draft))
    assert set_draft(s, req2, tmp_path/'draft-ops')['exists'] is False
    assert s.draft is None
    s.user_id = 10
    with pytest.raises(ValueError, match='different content or account'):
        set_draft(s, req, tmp_path/'draft-ops')


def test_reply_and_schedule_are_pinned_and_not_resent(tmp_path):
    s = Session(tmp_path)
    box = Outbox(tmp_path/'outbox', s.user_id)
    ident = uuid.uuid4().hex
    when = datetime.fromtimestamp(int(time.time()) + 3600, timezone.utc).isoformat()
    prepared = box.prepare(s, draft_id=ident, recipient='1', text='Tomorrow', file_path=None,
                           reply_to_message_id=5, schedule_at=when)
    assert prepared['reply_to_message_id'] == 5
    assert prepared['scheduled_at'] == int(datetime.fromisoformat(when).timestamp())
    assert not any(p['@type'] == 'sendMessage' for p in s.calls)
    sent = box.send(s, draft_id=ident)
    assert sent['status'] == 'scheduled'
    send = next(p for p in s.calls if p['@type'] == 'sendMessage')
    assert send['reply_to']['message_id'] == 5
    assert send['options']['scheduling_state']['send_date'] == prepared['scheduled_at']
    assert Outbox(box.directory, s.user_id).send(s, draft_id=ident)['status'] == 'scheduled'
    assert len([p for p in s.calls if p['@type'] == 'sendMessage']) == 1


def test_expired_schedule_never_falls_back_to_immediate_send(tmp_path, monkeypatch):
    s = Session(tmp_path)
    box = Outbox(tmp_path/'outbox', s.user_id)
    ident = uuid.uuid4().hex
    now = int(time.time())
    when = datetime.fromtimestamp(now + 100, timezone.utc).isoformat()
    box.prepare(s, draft_id=ident, recipient='1', text='Later', file_path=None, schedule_at=when)
    monkeypatch.setattr('telegram_search_mcp.outgoing.time.time', lambda: now + 90)
    with pytest.raises(ValueError, match='expired schedules'):
        box.send(s, draft_id=ident)
    assert not any(p['@type'] == 'sendMessage' for p in s.calls)
