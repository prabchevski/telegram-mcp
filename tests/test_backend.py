from __future__ import annotations

from typing import Any

import pytest

from telegram_search_mcp.backend import MediaError, MediaTooLargeError
from telegram_search_mcp.policy import Policy, PolicyError, new_policy
from telegram_search_mcp.tdjson import TdApi, TdlibSchema
from telegram_search_mcp.tdlib_backend import (
    CursorError,
    TDLibBackend,
    TdlibSession,
    _extract_text,
    _read_private_media_file,
    _select_media_candidate,
    _verified_media_mime,
    _verified_image_dimensions,
    decode_search_cursor,
    encode_search_cursor,
)
import telegram_search_mcp.tdlib_backend as backend_module


def td_message(
    message_id: int,
    *,
    date: int,
    chat_id: int = -100,
    text: str | None = None,
    caption: str | None = None,
) -> dict[str, Any]:
    if text is not None:
        content = {
            "@type": "messageText",
            "text": {"@type": "formattedText", "text": text, "entities": []},
        }
    elif caption is not None:
        content = {
            "@type": "messagePhoto",
            "caption": {"@type": "formattedText", "text": caption, "entities": []},
        }
    else:
        content = {"@type": "messageSticker"}
    return {
        "@type": "message",
        "id": message_id,
        "chat_id": chat_id,
        "date": date,
        "sender_id": {"@type": "messageSenderUser", "user_id": 42},
        "content": content,
    }


class FakeSession:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.user_id = policy.expected_user_id
        self.requests: list[dict[str, Any]] = []
        self.pages: dict[int, list[dict[str, Any]]] = {
            0: [
                td_message(30, date=300, text="newest"),
                td_message(20, date=200, caption="photo caption"),
                td_message(10, date=100),
            ],
            30: [
                td_message(30, date=300, text="newest"),
                td_message(20, date=200, caption="photo caption"),
                td_message(10, date=100),
            ],
            20: [td_message(20, date=200, caption="photo caption"), td_message(10, date=100)],
            10: [],
        }

    def request(self, request: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        self.requests.append(request)
        if request["@type"] != "searchMessages":
            raise AssertionError(request)
        offset_id = int(request.get("offset_message_id", 0))
        return {
            "@type": "messages",
            "total_count": 3,
            "messages": self.pages[offset_id],
        }

    def get_chat(self, chat_id: int, timeout: float = 10.0) -> dict[str, Any]:
        return {
            "@type": "chat",
            "id": chat_id,
            "title": "Example chat",
            "type": {"@type": "chatTypeSupergroup", "supergroup_id": 1},
        }


def configured_backend(monkeypatch: pytest.MonkeyPatch) -> tuple[TDLibBackend, FakeSession]:
    monkeypatch.setattr(backend_module, "SCHEMA", TdlibSchema.V1_8)
    policy = Policy(api_id=12345, expected_user_id=7)
    session = FakeSession(policy)
    monkeypatch.setattr(
        Policy, "load", classmethod(lambda cls, profile="default": policy)
    )
    backend = TDLibBackend()
    monkeypatch.setattr(
        backend, "_ready", lambda current, timeout=30.0: session
    )
    return backend, session


def test_global_request_uses_null_chat_list_and_preserves_raw_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, session = configured_backend(monkeypatch)
    page = backend._search_messages_sync(query="needle", cursor=None, limit=2)

    assert session.requests[0]["chat_list"] is None
    assert [item.text for item in page.items] == ["newest", "photo caption"]
    assert page.next_cursor is not None
    decoded = decode_search_cursor(page.next_cursor, "needle")
    assert decoded.offset_message_id == 20


def test_cursor_stops_at_last_consumed_message_not_raw_page_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = configured_backend(monkeypatch)
    page = backend._search_messages_sync(query="needle", cursor=None, limit=1)

    assert [item.message_id for item in page.items] == [30]
    assert page.next_cursor is not None
    assert decode_search_cursor(page.next_cursor, "needle").offset_message_id == 30


def test_boundary_message_is_deduplicated_on_next_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = configured_backend(monkeypatch)
    first = backend._search_messages_sync(query="needle", cursor=None, limit=1)
    second = backend._search_messages_sync(
        query="needle", cursor=first.next_cursor, limit=2
    )

    assert [item.message_id for item in second.items] == [20]


def test_cursor_is_bound_to_the_exact_query() -> None:
    cursor = TdApi(TdlibSchema.V1_8).parse_search_messages(
        {
            "@type": "messages",
            "messages": [td_message(5, date=100, text="x")],
        }
    ).next_cursor
    assert cursor is not None
    encoded = encode_search_cursor(cursor, "alpha")
    with pytest.raises(CursorError):
        decode_search_cursor(encoded, "beta")


def test_message_text_and_caption_are_supported_without_media() -> None:
    assert _extract_text(td_message(1, date=1, text="hello")["content"]) == (
        "hello",
        "messageText",
    )
    assert _extract_text(td_message(2, date=2, caption="caption")["content"]) == (
        "caption",
        "messagePhoto",
    )
    assert _extract_text(td_message(3, date=3)["content"]) is None


def photo_content(*, small_size: int = 512, large_size: int = 4_096) -> dict[str, Any]:
    return {
        "@type": "messagePhoto",
        "photo": {
            "sizes": [
                {
                    "width": 320,
                    "height": 200,
                    "photo": {"id": 11, "size": small_size},
                },
                {
                    "width": 1_920,
                    "height": 1_080,
                    "photo": {"id": 22, "size": large_size},
                },
            ]
        },
    }


def test_photo_preview_uses_largest_size_that_fits_the_limit() -> None:
    preview = _select_media_candidate(
        photo_content(), quality="preview", max_bytes=1_024
    )
    full = _select_media_candidate(
        photo_content(), quality="full", max_bytes=8_192
    )

    assert preview is not None
    assert preview.file["id"] == 11
    assert preview.is_preview is True
    assert full is not None
    assert full.file["id"] == 22
    assert full.is_preview is False


def test_photo_preview_prefers_known_fitting_size_over_unknown_large_size() -> None:
    preview = _select_media_candidate(
        photo_content(large_size=0), quality="preview", max_bytes=1_024
    )

    assert preview is not None
    assert preview.file["id"] == 11


def test_image_documents_are_not_sent_to_the_native_image_decoder() -> None:
    content = {
        "@type": "messageDocument",
        "document": {
            "mime_type": "image/png",
            "file_name": "untrusted.png",
            "document": {"id": 44, "size": 100},
        },
    }

    assert _select_media_candidate(content, quality="full", max_bytes=1_024) is None


def test_media_magic_must_match_the_declared_type() -> None:
    assert _verified_media_mime(b"\xff\xd8\xffvalid", "image/jpeg") == "image/jpeg"
    with pytest.raises(MediaError, match="does not match"):
        _verified_media_mime(b"not-a-jpeg", "image/jpeg")


def test_native_image_dimensions_reject_a_png_pixel_bomb() -> None:
    pixel_bomb = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + (100_000).to_bytes(4, "big")
        + (100_000).to_bytes(4, "big")
    )

    with pytest.raises(MediaError, match="dimensions exceed"):
        _verified_image_dimensions(pixel_bomb, "image/png")


def test_native_image_dimensions_are_read_from_jpeg_bytes() -> None:
    jpeg_with_sof = (
        b"\xff\xd8"
        b"\xff\xc0\x00\x11\x08"
        b"\x03\x20"
        b"\x04\xb0"
        b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )

    assert _verified_image_dimensions(jpeg_with_sof, "image/jpeg") == (1200, 800)


def test_private_media_reader_rejects_paths_outside_tdlib_root(tmp_path: Any) -> None:
    root = tmp_path / "tdlib-files"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8\xffoutside")

    with pytest.raises(MediaError, match="unsafe media path"):
        _read_private_media_file(str(outside), root=root, max_bytes=1_024)


def test_private_media_reader_enforces_the_byte_limit(tmp_path: Any) -> None:
    root = tmp_path / "tdlib-files"
    root.mkdir()
    oversized = root / "oversized.jpg"
    oversized.write_bytes(b"\xff\xd8\xff" + b"x" * 64)

    with pytest.raises(MediaTooLargeError, match="exceeds"):
        _read_private_media_file(str(oversized), root=root, max_bytes=32)


def test_profile_file_is_private_and_account_bound(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o755)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("TGSEARCH_DATA_DIR", str(workspace))
    policy = new_policy(12345)
    policy.bind_account(98765)
    policy.save()

    loaded = Policy.load()
    assert loaded.expected_user_id == 98765
    profile_file = (
        fake_home
        / "Library"
        / "Application Support"
        / "TelegramSearchMCPShared"
        / "profiles"
        / "default"
        / "policy.json"
    )
    assert profile_file.stat().st_mode & 0o777 == 0o600
    assert profile_file.parent.stat().st_mode & 0o777 == 0o700
    assert workspace.stat().st_mode & 0o777 == 0o755
    assert not (workspace / "profiles").exists()

    profile_file.chmod(0o644)
    with pytest.raises(PolicyError):
        Policy.load()


def test_failed_session_initialization_releases_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = Policy(api_id=12345, expected_user_id=7)

    class FailingSession:
        last: "FailingSession | None" = None

        def __init__(self, current: Policy, profile: str) -> None:
            self.policy = current
            self.user_id = None
            self.closed = False
            FailingSession.last = self

        def require_ready(self, timeout: float = 30.0) -> None:
            raise RuntimeError("setup failed")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(backend_module, "TdlibSession", FailingSession)
    backend = TDLibBackend()
    with pytest.raises(RuntimeError, match="setup failed"):
        backend._ready(policy)

    assert backend._session is None
    assert FailingSession.last is not None
    assert FailingSession.last.closed is True


def test_runtime_version_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = TdlibSession(Policy(api_id=12345))
    monkeypatch.setattr(
        session,
        "request",
        lambda request, timeout=30.0: {
            "@type": "optionValueString",
            "value": "1.8.66",
        },
    )
    with pytest.raises(RuntimeError, match="Unsupported TDLib runtime version"):
        session.verify_runtime_version()


def test_unconfirmed_native_close_retains_actual_profile_lock(monkeypatch, tmp_path):
    from concurrent.futures import Future
    from types import SimpleNamespace
    from telegram_search_mcp.tdlib_backend import SessionBusyError, SessionCloseError

    closed = SimpleNamespace(confirmed=False)
    closed.wait = lambda timeout: closed.confirmed
    stopped = []

    def send(request):
        assert request == {'@type': 'close'}
        result = Future()
        result.set_result({'@type': 'ok'})
        return result

    monkeypatch.setattr(backend_module, 'lock_path', lambda profile: tmp_path / 'tdlib.lock')
    session = TdlibSession(Policy(api_id=12345))
    session._acquire_profile_lock()
    session.client = SimpleNamespace(send=send, stop=lambda: stopped.append(True))
    session.authorization = SimpleNamespace(closed=closed)
    other = TdlibSession(Policy(api_id=12345))
    try:
        with pytest.raises(SessionCloseError, match='retained'):
            session.close()
        assert not stopped
        with pytest.raises(SessionBusyError):
            other._acquire_profile_lock()
        with pytest.raises(SessionCloseError, match='closing'):
            session.open()
        closed.confirmed = True
        session.close()
        assert stopped == [True]
        other._acquire_profile_lock()
    finally:
        closed.confirmed = True
        session.close()
        other.close()


def test_backend_keeps_session_reference_if_native_close_is_uncertain():
    from telegram_search_mcp.tdlib_backend import SessionCloseError

    class ClosingSession:
        def close(self):
            raise SessionCloseError('native closure not confirmed')

    backend = TDLibBackend()
    original = backend._session = ClosingSession()
    with pytest.raises(SessionCloseError):
        backend.close_sync()
    assert backend._session is original


def test_failed_initialization_keeps_uncertain_native_candidate(monkeypatch):
    from telegram_search_mcp.tdlib_backend import SessionCloseError

    class FailingSession:
        def __init__(self, policy, profile):
            pass

        def require_ready(self, timeout):
            raise RuntimeError('initialization failed')

        def close(self):
            raise SessionCloseError('native closure not confirmed')

    monkeypatch.setattr(backend_module, 'TdlibSession', FailingSession)
    backend = TDLibBackend()
    with pytest.raises(SessionCloseError):
        backend._ready(Policy(api_id=12345, expected_user_id=7))
    assert isinstance(backend._session, FailingSession)
