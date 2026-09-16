"""Native Telegram text drafts, with observed-version checks and durable dispatch IDs."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Literal

from pydantic import Field
from .models import OutputModel, TrustBoundary, UntrustedText
from .navigation import Request, ChatId, Id, TopicId, cloud_chat, text, topic
from .outgoing import valid_id, validate_content
from .paths import ensure_private_dir
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError


class DraftRequest(Request):
    chat_id: ChatId
    topic_id: TopicId | None = None


class SetDraftRequest(DraftRequest):
    operation_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    text: str = Field(max_length=4096)
    expected_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    reply_to_message_id: Id | None = None


class DraftResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    chat_id: int
    topic_id: int | None
    exists: bool
    text: UntrustedText
    reply_to_message_id: int | None
    content_type: str | None
    version: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['current', 'stored', 'unknown', 'failed'] = 'current'
    operation_id: str | None = None


def version(draft) -> str:
    return hashlib.sha256(json.dumps(draft, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def current(session, request: DraftRequest) -> dict | None:
    chat = cloud_chat(session, request.chat_id)
    if request.topic_id:
        info = session.request({'@type': 'getForumTopic', 'chat_id': request.chat_id,
                                'forum_topic_id': request.topic_id})
        return info.get('draft_message')
    return chat.get('draft_message')


def result(request, draft, *, status='current', operation_id=None) -> dict:
    content = (draft or {}).get('content') or (draft or {}).get('input_message_text') or {}
    return DraftResult(chat_id=request.chat_id, topic_id=request.topic_id, exists=draft is not None,
        text=text(str((content.get('text') or {}).get('text', ''))),
        reply_to_message_id=((draft or {}).get('reply_to') or {}).get('message_id'),
        content_type=content.get('@type'), version=version(draft), status=status,
        operation_id=operation_id).model_dump(mode='json')


def reply(session, chat_id: int, message_id: int | None, topic_id: int | None = None):
    if message_id is None:
        return None
    message = session.request({'@type': 'getMessage', 'chat_id': chat_id, 'message_id': message_id})
    if message.get('chat_id') != chat_id or message.get('id') != message_id:
        raise ValueError('Reply target does not belong to the selected chat')
    if topic_id and (message.get('topic_id') or {}).get('forum_topic_id') != topic_id:
        raise ValueError('Reply target does not belong to the selected forum topic')
    props = session.request({'@type': 'getMessageProperties', 'chat_id': chat_id, 'message_id': message_id})
    if props.get('can_be_replied') is not True:
        raise ValueError('Telegram does not allow replying to this message')
    return {'@type': 'inputMessageReplyToMessage', 'message_id': message_id,
            'quote': None, 'checklist_task_id': 0, 'poll_option_id': ''}


def set_draft(session, request: SetDraftRequest, directory: Path) -> dict:
    # An operation ID is never dispatched twice, even if a response was lost.
    valid_id(request.operation_id)
    if request.text:
        validate_content(request.text, None)
    elif request.reply_to_message_id is not None:
        raise ValueError('An empty text clears the draft; omit reply_to_message_id')
    ensure_private_dir(directory)
    path = directory / (request.operation_id + '.json')
    spec = request.model_dump()
    if path.exists():
        _assert_private_file(path)
        if path.stat().st_size > 65536:
            raise ValueError('Invalid draft operation record')
        previous = json.loads(path.read_text())
        if previous['user_id'] != session.user_id or previous['spec'] != spec:
            raise ValueError('operation_id belongs to different content or account')
        return previous['result']
    existing = current(session, request)
    if version(existing) != request.expected_version:
        raise ValueError('Telegram draft changed. Read it again before replacing it')
    reply_to = reply(session, request.chat_id, request.reply_to_message_id, request.topic_id)
    draft = None if not request.text else {'@type': 'draftMessage', 'date': int(time.time()),
        'reply_to': reply_to, 'content': {'@type': 'draftMessageContentText',
        'text': {'@type': 'formattedText', 'text': request.text, 'entities': []}, 'link_preview_options': None},
        'effect_id': 0, 'suggested_post_info': None}
    value = {'user_id': session.user_id, 'spec': spec,
             'result': result(request, draft, status='unknown', operation_id=request.operation_id)}
    def save():
        _atomic_private_json(path, value)
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    save()
    try:
        response = session.request({'@type': 'setChatDraftMessage', 'chat_id': request.chat_id,
            'topic_id': topic(request.topic_id), 'draft_message': draft})
        if response.get('@type') == 'ok':
            value['result']['status'] = 'stored'
    except TdlibError:
        value['result']['status'] = 'failed'
    except (TimeoutError, RuntimeError):
        pass
    save()
    return value['result']
