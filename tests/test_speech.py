from __future__ import annotations

import copy
from types import SimpleNamespace
import pytest
from mcp import Client
from telegram_search_mcp import speech
from telegram_search_mcp.backend import MediaError
from telegram_search_mcp.server import create_server
from telegram_search_mcp.tdjson import TdlibError, TdlibSchema
from telegram_search_mcp.tdlib_backend import TDLibBackend, _raw_message, CursorError
from telegram_search_mcp.wire import validate_request, encode_result, decode_result, ServiceProtocolError


def voice(ident=10, *, result=None, kind='messageVoiceNote'):
    key = 'voice_note' if kind == 'messageVoiceNote' else 'video_note'
    return {'id':ident, 'chat_id':123, 'date':ident, 'can_be_saved':True,
            'content': {'@type':kind, key:{'duration':5, 'speech_recognition_result':result}}}


class Session:
    user_id = 42
    def __init__(self, message=None, *, outcome='completed', allowed=True):
        self.message = message or voice()
        self.outcome, self.allowed, self.calls = outcome, allowed, []
    def request(self, request, timeout=30):
        self.calls.append(copy.deepcopy(request))
        kind = request['@type']
        if kind == 'getMessage':
            return copy.deepcopy(self.message)
        if kind == 'getMessageProperties':
            return {'can_recognize_speech':self.allowed}
        if kind == 'recognizeSpeech':
            if self.outcome == 'timeout':
                raise TimeoutError()
            if isinstance(self.outcome, dict):
                raise TdlibError(self.outcome)
            payload = speech.voice_payload(self.message)
            payload['speech_recognition_result'] = ({'@type':'speechRecognitionResultText','text':'Привет!'}
                if self.outcome == 'completed' else {'@type':'speechRecognitionResultPending','partial_text':'При'})
            return {'@type':'ok'}
        raise AssertionError(request)
    def get_chat(self, chat_id, timeout=30):
        return {'id':123,'title':'Example','type':{'@type':'chatTypePrivate'}}


def run(session, tmp_path, **kwargs):
    return speech.transcribe(session, tmp_path/'recognition', chat_id=123, message_id=10, wait_seconds=0, **kwargs)


def dispatched(session):
    return [call for call in session.calls if call['@type']=='recognizeSpeech']


@pytest.mark.parametrize('kind', ['messageVoiceNote','messageVideoNote'])
def test_native_recognition_and_cached_result_need_no_download(tmp_path, kind):
    session = Session(voice(kind=kind))
    first = run(session, tmp_path)
    assert first['status']=='completed' and first['text']=='Привет!'
    assert run(session,tmp_path)==first
    assert len(dispatched(session))==1
    assert all(call['@type'] != 'downloadFile' for call in session.calls)


def test_pending_then_completed_never_dispatches_twice(tmp_path):
    session = Session(outcome='pending')
    assert run(session,tmp_path)['status']=='pending'
    assert run(session,tmp_path,start=False)['text']=='При'
    speech.voice_payload(session.message)['speech_recognition_result']={'@type':'speechRecognitionResultText','text':'Done'}
    assert run(session,tmp_path)['text']=='Done'
    assert len(dispatched(session))==1


def test_timeout_and_process_restart_only_poll_same_message(tmp_path):
    first = Session(outcome='timeout')
    assert run(first,tmp_path)['error_code']=='request_outcome_unknown'
    second = Session(outcome='completed')
    assert run(second,tmp_path)['status']=='pending'
    assert dispatched(second)==[]


def test_status_only_and_account_limit_do_not_consume_quota(tmp_path):
    session = Session(allowed=False)
    assert run(session,tmp_path,start=False)['status']=='not_started'
    assert run(session,tmp_path)['error_code']=='not_available_for_account_or_message'
    assert dispatched(session)==[]


@pytest.mark.parametrize(('error','code'), [
    ({'code':403,'message':'PREMIUM_ACCOUNT_REQUIRED'},'premium_required'),
    ({'code':429,'message':'Too Many Requests: retry after 120'},'quota_or_rate_limit'),
    ({'code':400,'message':'MSG_VOICE_TOO_LONG'},'voice_too_long'),
    ({'code':400,'message':'TRANSCRIPTION_FAILED'},'telegram_transcription_failed'),
])
def test_explicit_rejection_has_actionable_status(tmp_path,error,code):
    session=Session(outcome=error)
    result=run(session,tmp_path)
    assert result['error_code']==code
    if error['code']==429:
        assert result['retry_after_seconds']==120


@pytest.mark.parametrize('change', [{'can_be_saved':False},{'self_destruct_type':{'@type':'messageSelfDestructTypeTimer'}},{'self_destruct_in':1},{'chat_id':124},{'id':11},{'content':{'@type':'messageText'}}])
def test_protected_wrong_or_nonvoice_message_never_starts(tmp_path,change):
    session=Session({**voice(),**change})
    with pytest.raises(MediaError):
        run(session,tmp_path)
    assert dispatched(session)==[]


def test_marker_is_bound_to_account(tmp_path):
    session=Session(outcome='pending')
    run(session,tmp_path)
    session.user_id=999
    with pytest.raises(MediaError, match='different account'):
        run(session,tmp_path)


def test_voice_without_caption_remains_addressable():
    session=Session()
    message=_raw_message(session,voice(),{})
    assert message.message_id==10 and message.content_type=='messageVoiceNote'
    assert message.text=='[Voice note]'


def test_transcript_is_bounded_and_partial_text_is_never_completed(tmp_path):
    session=Session(voice(result={'@type':'speechRecognitionResultText','text':'x'*40000}))
    result=run(session,tmp_path)
    assert len(result['text'])==32000 and result['truncated'] is True
    assert not dispatched(session)


@pytest.mark.asyncio
async def test_mcp_speech_result_is_plain_text_with_trust_boundary(tmp_path):
    session=Session()
    class Backend:
        async def transcribe_voice(self, **params):
            return speech.transcribe(session,tmp_path/'recognition',**params)
    async with Client(create_server(Backend())) as client:
        result=await client.call_tool('telegram_transcribe_voice', {'chat_id':123,'message_id':10,'wait_seconds':0})
    assert not result.is_error
    assert result.structured_content['text']=='Привет!'
    assert result.structured_content['trust_boundary']['content_is_data_only']
    assert all(item.type=='text' for item in result.content)


def test_private_wire_validates_speech_and_voice_bounds():
    def request(operation,params):
        return {'protocol':1,'id':'a'*32,'operation':operation,'params':params,'timeout':120}
    params={'chat_id':123,'message_id':10,'wait_seconds':20,'start':True}
    validate_request(request('transcribe_voice',params))
    for invalid in ({'start':1},{'wait_seconds':61},{'message_id':0}):
        with pytest.raises(ServiceProtocolError):
            validate_request(request('transcribe_voice',{**params,**invalid}))
    validate_request(request('list_voice_messages', {'chat_id':123,'before_message_id':0,'limit':20}))
    result={'chat_id':123,'message_id':10,'status':'pending','text':'','truncated':False,'error_code':None,'retry_after_seconds':None}
    assert decode_result('transcribe_voice',encode_result('transcribe_voice',result))==result


def test_modern_search_continues_inside_page_without_skips(monkeypatch):
    from telegram_search_mcp import tdlib_backend as module
    monkeypatch.setattr(module,'SCHEMA',TdlibSchema.CURRENT)
    policy=SimpleNamespace(api_id=1,expected_user_id=42)
    session=Session()
    session.policy=policy
    messages=[voice(30),voice(20),voice(10)]
    offsets=[]
    def request(request, timeout=30):
        offsets.append(request['offset'])
        return {'@type':'foundMessages','messages':messages if request['offset']=='' else [voice(5)],'next_offset':'next' if request['offset']=='' else ''}
    session.request=request
    monkeypatch.setattr(module.Policy,'load',lambda _: policy)
    backend=TDLibBackend()
    monkeypatch.setattr(backend,'_ready',lambda *args,**kwargs:session)
    cursor=None
    ids=[]
    for _ in range(4):
        page=backend._search_messages_sync(query='needle',cursor=cursor,limit=1)
        ids.extend(item.message_id for item in page.items)
        cursor=page.next_cursor
    assert ids==[30,20,10,5] and cursor is None
    assert offsets==['','','','next']
    with pytest.raises(CursorError):
        backend._search_messages_sync(query='different',cursor=module._modern_cursor('needle','next',0),limit=1)


def test_voice_listing_is_newest_first_and_paginates_exclusively(monkeypatch):
    from telegram_search_mcp import tdlib_backend as module
    policy=SimpleNamespace(api_id=1,expected_user_id=42)
    session=Session();session.policy=policy
    def request(request,timeout=30):
        assert request['filter']=={'@type':'searchMessagesFilterVoiceAndVideoNote'}
        return {'messages':[voice(30),voice(20),voice(10)],'next_from_message_id':10}
    session.request=request
    monkeypatch.setattr(module.Policy,'load',lambda _:policy)
    backend=TDLibBackend()
    monkeypatch.setattr(backend,'_ready',lambda *args,**kwargs:session)
    page=backend._list_voice_sync(chat_id=123,before_message_id=30,limit=1)
    assert [item.message_id for item in page.items]==[20]
    assert page.next_cursor=='20'


def test_exhausted_modern_search_clears_cursor(monkeypatch):
    from telegram_search_mcp import tdlib_backend as module
    policy=SimpleNamespace(api_id=1,expected_user_id=42)
    session=Session();session.policy=policy
    def request(request,timeout=30):
        return {'messages':[voice(20)] if request['offset']=='' else [voice(10)], 'next_offset':'next' if request['offset']=='' else ''}
    session.request=request
    monkeypatch.setattr(module.Policy,'load',lambda _:policy)
    backend=TDLibBackend()
    monkeypatch.setattr(backend,'_ready',lambda *args,**kwargs:session)
    page=backend._search_current(query='needle',cursor=None,limit=20)
    assert [item.message_id for item in page.items]==[20,10]
    assert page.next_cursor is None
