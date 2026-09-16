"""Bounded TDLib reads, native speech recognition and optional outgoing operations."""

from __future__ import annotations

import asyncio
import base64
import binascii
import fcntl
import hashlib
import json
import mimetypes
import os
import queue
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import __version__
from .backend import (
    MediaError,
    MediaKind,
    MediaQuality,
    MediaTooLargeError,
    RawMedia,
    RawMessage,
    RawMessagePage,
)
from .keychain import get_secret
from .paths import database_dir, ensure_runtime_layout, files_dir, lock_path
from .policy import Policy, PolicyError
from .tdjson import (
    AuthorizationController,
    AuthorizationMachine,
    CtypesTdJsonTransport,
    GlobalSearchCursor,
    TdApi,
    TdClient,
    TdlibError,
    TdlibParameters,
    TdlibSchema,
)

from .native_runtime import VERSION as EXPECTED_TDLIB_VERSION

SCHEMA = TdlibSchema.CURRENT
CURSOR_VERSION = 1
MAX_RAW_PAGES = 5
RAW_PAGE_SIZE = 50
OPERATION_TIMEOUT = 50.0
HARD_MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 40_000_000

_AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/x-m4a",
}

_TRANSIENT_AUTH_STATES = {
    "authorizationStateWaitTdlibParameters",
    "authorizationStateWaitEncryptionKey",
}


class SetupRequiredError(RuntimeError):
    pass


class SessionBusyError(RuntimeError):
    pass


class SessionCloseError(RuntimeError):
    """The native client has not confirmed closure; keep its database lock."""


class CursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _MediaCandidate:
    file: dict[str, Any]
    media_kind: MediaKind
    mime_type: str
    file_name: str | None = None
    width: int | None = None
    height: int | None = None
    is_preview: bool = False


class TdlibSession:
    """One locked local TDLib session with no Telegram mutation helpers."""

    def __init__(self, policy: Policy, profile: str = "default") -> None:
        self.policy = policy
        self.profile = profile
        self.transport: CtypesTdJsonTransport | None = None
        self.client: TdClient | None = None
        self.authorization: AuthorizationController | None = None
        self.user_id: int | None = None
        self._lock_handle: TextIO | None = None
        self._state_lock = threading.RLock()
        self._closing = False

    def open(self) -> None:
        with self._state_lock:
            if self._closing:
                raise SessionCloseError("Telegram session is closing; restart the local service")
            if self.client is not None:
                return
            ensure_runtime_layout(self.profile)
            self._acquire_profile_lock()
            try:
                api_hash = get_secret("api_hash", self.profile)
                encoded_key = get_secret("database_key", self.profile)
                if not api_hash or not encoded_key:
                    raise SetupRequiredError(
                        "Telegram credentials are missing. Run `tgsearch auth` locally."
                    )
                parameters = TdlibParameters(
                    api_id=self.policy.api_id,
                    api_hash=api_hash,
                    database_directory=str(database_dir(self.profile)),
                    files_directory=str(files_dir(self.profile)),
                    database_encryption_key=_decode_database_key(encoded_key),
                    use_secret_chats=False,
                    device_model="Shared local read-only Telegram search",
                    application_version=__version__,
                )
                self.transport = CtypesTdJsonTransport(log_verbosity=0)
                from .native_runtime import verify
                verify(self.transport.library_path)
                self.client = TdClient(self.transport)
                machine = AuthorizationMachine(parameters, SCHEMA)
                self.authorization = AuthorizationController(self.client, machine)
                self.authorization.begin()
            except BaseException:
                # Once a client can have received database parameters, release
                # the profile only after TDLib itself confirms it is closed.
                if self.client is not None:
                    self.close()
                else:
                    self._close_components()
                raise

    @property
    def state(self) -> dict[str, Any] | None:
        return self.authorization.state if self.authorization is not None else None

    def wait_for_settled_state(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait through automatic init states and return ready or interactive state."""

        self.open()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_background_error()
            state = self.state
            state_type = state.get("@type") if state else None
            if state_type == "authorizationStateReady":
                return state
            if state_type and state_type not in _TRANSIENT_AUTH_STATES:
                return state
            time.sleep(0.05)
        raise TimeoutError("Timed out while initializing the local Telegram session")

    def wait_for_state_change(
        self, previous: dict[str, Any] | None, timeout: float = 300.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_background_error()
            state = self.state
            if state is not None and state != previous:
                return state
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for Telegram authorization")

    def require_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        state = self.wait_for_settled_state(_remaining(deadline, timeout))
        if state.get("@type") != "authorizationStateReady":
            raise SetupRequiredError(
                "Telegram authorization is incomplete. Run `tgsearch auth` locally."
            )
        self.verify_runtime_version(timeout=_remaining(deadline, 10.0))
        me = self.request(
            {"@type": "getMe"}, timeout=_remaining(deadline, 10.0)
        )
        user_id = int(me.get("id", 0))
        if user_id <= 0:
            raise RuntimeError("TDLib returned an invalid account identifier")
        if (
            self.policy.expected_user_id is not None
            and user_id != self.policy.expected_user_id
        ):
            raise PolicyError("Authorized Telegram account does not match this profile")
        self.user_id = user_id

    def verify_runtime_version(self, timeout: float = 10.0) -> None:
        response = self.request(
            {"@type": "getOption", "name": "version"}, timeout=timeout
        )
        version = (
            str(response.get("value", ""))
            if response.get("@type") == "optionValueString"
            else ""
        )
        if version != EXPECTED_TDLIB_VERSION:
            raise RuntimeError(
                "Unsupported TDLib runtime version; expected "
                f"{EXPECTED_TDLIB_VERSION}, got {version or 'unknown'}"
            )

    def request(self, request: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("TDLib session is not open")
        return self.client.request(request, timeout=timeout)

    def get_chat(self, chat_id: int, timeout: float = 10.0) -> dict[str, Any]:
        return self.request(TdApi.get_chat(chat_id), timeout=timeout)

    def close(self) -> None:
        with self._state_lock:
            if self.client is not None:
                self._closing = True
                try:
                    future = self.client.send(TdApi.close())
                    future.result(timeout=5.0)
                except BaseException:
                    pass
                if self.authorization is None or not self.authorization.closed.wait(10.0):
                    # Keep the receiver, session reference and flock alive.
                    # The daemon retires after this failure; the OS releases
                    # the lock on exit if TDLib still cannot confirm closure.
                    raise SessionCloseError(
                        "TDLib has not confirmed closure; the profile lock is retained until the service exits"
                    )
            self._close_components()

    def _raise_background_error(self) -> None:
        if self.authorization is not None:
            try:
                raise self.authorization.automatic_errors.get_nowait()
            except queue.Empty:
                pass
        if self.client is not None:
            try:
                raise self.client.handler_errors.get_nowait()
            except queue.Empty:
                pass

    def _acquire_profile_lock(self) -> None:
        path = lock_path(self.profile)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        stat = os.fstat(descriptor)
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            os.close(descriptor)
            raise RuntimeError("TDLib profile lock has unsafe ownership or permissions")
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise SessionBusyError(
                "Telegram profile is in use by another local process"
            ) from exc
        self._lock_handle = handle

    def _close_components(self) -> None:
        if self.client is not None:
            self.client.stop()
        self.authorization = None
        self.client = None
        self.transport = None
        self._closing = False
        if self._lock_handle is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_handle.close()
                self._lock_handle = None


class TDLibBackend:
    """Global cloud-chat search with bounded results and serialized TDLib RPCs."""

    def __init__(self, profile: str = "default") -> None:
        self.profile = profile
        self._session: TdlibSession | None = None
        self._session_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._outbox = None

    async def search_messages(
        self, *, query: str, cursor: str | None, limit: int
    ) -> RawMessagePage:
        return await asyncio.to_thread(
            self._search_messages_sync,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    async def list_voice_messages(self, *, chat_id: int, before_message_id: int = 0, limit: int = 10) -> RawMessagePage:
        return await asyncio.to_thread(self._list_voice_sync, chat_id=chat_id, before_message_id=before_message_id, limit=limit)

    def _list_voice_sync(self, *, chat_id: int, before_message_id: int, limit: int) -> RawMessagePage:
        if not 1 <= limit <= 20 or before_message_id < 0:
            raise ValueError("Invalid voice-list bounds")
        policy = Policy.load(self.profile)
        with self._operation_lock:
            session = self._ready(policy)
            chat = session.get_chat(chat_id)
            _reject_secret_chat(chat)
            response = session.request(TdApi(SCHEMA).search_chat_messages(
                chat_id, "", from_message_id=before_message_id, limit=limit + 1,
                filter_={"@type": "searchMessagesFilterVoiceAndVideoNote"}))
            items = []
            for message in response.get("messages", []):
                if message.get("chat_id") != chat_id or (before_message_id and message.get("id", 0) >= before_message_id):
                    continue
                from .speech import voice_payload
                if voice_payload(message) is None:
                    continue
                item = _raw_message(session, message, {chat_id: str(chat.get("title", ""))})
                if item is not None:
                    items.append(item)
            items.sort(key=lambda item: (item.sent_at, item.message_id), reverse=True)
            selected = items[:limit]
            next_id = selected[-1].message_id if selected and (len(items) >= limit or response.get("next_from_message_id")) else 0
            self._verify_profile(policy, session)
            return RawMessagePage(tuple(selected), str(next_id) if next_id else None)

    async def transcribe_voice(self, *, chat_id: int, message_id: int, wait_seconds: int = 20, start: bool = True) -> dict:
        return await asyncio.to_thread(self._transcribe_sync, chat_id=chat_id, message_id=message_id, wait_seconds=wait_seconds, start=start)

    def _transcribe_sync(self, **params) -> dict:
        from .paths import profile_root
        from .speech import transcribe
        policy = Policy.load(self.profile)
        with self._operation_lock:
            session = self._ready(policy)
            _reject_secret_chat(session.get_chat(params["chat_id"]))
            result = transcribe(session, profile_root(self.profile) / "recognition", **params)
            self._verify_profile(policy, session)
            return result

    async def get_message(
        self, *, chat_id: int, message_id: int
    ) -> RawMessage | None:
        return await asyncio.to_thread(
            self._get_message_sync, chat_id=chat_id, message_id=message_id
        )

    async def get_context(
        self, *, chat_id: int, message_id: int, before: int, after: int
    ) -> Sequence[RawMessage]:
        return await asyncio.to_thread(
            self._get_context_sync,
            chat_id=chat_id,
            message_id=message_id,
            before=before,
            after=after,
        )

    async def get_media(
        self,
        *,
        chat_id: int,
        message_id: int,
        quality: MediaQuality,
        max_bytes: int,
    ) -> RawMedia | None:
        return await asyncio.to_thread(
            self._get_media_sync,
            chat_id=chat_id,
            message_id=message_id,
            quality=quality,
            max_bytes=max_bytes,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self.close_sync)

    async def prepare_message(self, *, draft_id: str, recipient: str, text: str, file_path: str | None) -> dict:
        return await asyncio.to_thread(self._outgoing_sync, "prepare", draft_id=draft_id, recipient=recipient, text=text, file_path=file_path)

    async def send_message(self, *, draft_id: str) -> dict:
        return await asyncio.to_thread(self._outgoing_sync, "send", draft_id=draft_id)

    async def get_send_status(self, *, draft_id: str) -> dict:
        return await asyncio.to_thread(self._outgoing_sync, "status", draft_id=draft_id)

    def _outgoing_sync(self, operation: str, **params) -> dict:
        from .outgoing import Outbox
        from .paths import profile_root
        from .sending_settings import sending_enabled
        if operation != "status" and not sending_enabled():
            raise ValueError("Sending is disabled. Enable it locally with `tgsearch sending on` first")
        with self._operation_lock:
            policy = Policy.load(self.profile)
            session = self._ready(policy)
            if self._outbox is None:
                self._outbox = Outbox(profile_root(self.profile) / "outbox", session.user_id)
            if self._outbox.user_id != session.user_id:
                raise ValueError("Outgoing account changed; restart the service")
            self._outbox.attach(session.client)
            return getattr(self._outbox, operation)(session, **params)

    async def check_ready(self) -> dict[str, bool]:
        """Check saved authorization through the same serialized TDLib session."""
        return await asyncio.to_thread(self._check_ready_sync)

    def _check_ready_sync(self) -> dict[str, bool]:
        with self._operation_lock:
            self._ready(Policy.load(self.profile))
        return {"ready": True}

    def close_sync(self) -> None:
        with self._operation_lock:
            with self._session_lock:
                if self._session is not None:
                    self._session.close()
                    self._session = None

    def _ready(self, policy: Policy, timeout: float = 30.0) -> TdlibSession:
        if policy.expected_user_id is None:
            raise SetupRequiredError(
                "Telegram profile is not account-bound. Run `tgsearch auth` locally."
            )
        with self._session_lock:
            if self._session is None:
                candidate = TdlibSession(policy, self.profile)
                self._session = candidate
                try:
                    candidate.require_ready(timeout=timeout)
                except BaseException:
                    candidate.close()
                    self._session = None
                    raise
            session = self._session
            if session.policy.api_id != policy.api_id:
                raise PolicyError("Runtime api_id changed; restart the MCP server")
            if session.user_id != policy.expected_user_id:
                raise PolicyError("Authorized Telegram account does not match current profile")
            return session

    def _search_messages_sync(
        self, *, query: str, cursor: str | None, limit: int
    ) -> RawMessagePage:
        if SCHEMA is TdlibSchema.CURRENT:
            return self._search_current(query=query, cursor=cursor, limit=limit)
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        offset = decode_search_cursor(cursor, normalized)
        policy = Policy.load(self.profile)
        with self._operation_lock:
            deadline = time.monotonic() + OPERATION_TIMEOUT
            session = self._ready(policy, timeout=_remaining(deadline, 30.0))
            api = TdApi(SCHEMA)
            title_cache: dict[int, str] = {}
            results: list[RawMessage] = []
            seen: set[tuple[int, int]] = set()
            last_consumed: GlobalSearchCursor | None = None
            current = offset
            initial_boundary = _cursor_tuple(offset)
            visited_boundaries = {initial_boundary}

            for _ in range(MAX_RAW_PAGES):
                response = session.request(
                    api.search_messages(
                        normalized,
                        cursor=current,
                        limit=RAW_PAGE_SIZE,
                        chat_list="all",
                    ),
                    timeout=_remaining(deadline, 15.0),
                )
                page = api.parse_search_messages(response)
                if not page.messages:
                    break
                page_advanced = False
                for message in page.messages:
                    raw_cursor = _cursor_from_message(message)
                    raw_boundary = _cursor_tuple(raw_cursor)
                    if raw_boundary in visited_boundaries:
                        continue
                    visited_boundaries.add(raw_boundary)
                    page_advanced = True
                    last_consumed = raw_cursor
                    current = raw_cursor
                    key = (int(message.get("chat_id", 0)), int(message.get("id", 0)))
                    if key in seen:
                        continue
                    seen.add(key)
                    parsed = _raw_message(
                        session, message, title_cache, deadline=deadline
                    )
                    if parsed is not None:
                        results.append(parsed)
                    if len(results) >= limit:
                        break
                if len(results) >= limit or not page_advanced:
                    break

            self._verify_profile(policy, session)
            next_cursor = (
                encode_search_cursor(last_consumed, normalized)
                if last_consumed is not None
                and _cursor_tuple(last_consumed) != initial_boundary
                else None
            )
            return RawMessagePage(tuple(results[:limit]), next_cursor)

    def _search_current(self, *, query: str, cursor: str | None, limit: int) -> RawMessagePage:
        normalized = query.strip()
        if not normalized or not 1 <= limit <= 20:
            raise ValueError("Invalid search bounds")
        offset, skip = "", 0
        if cursor:
            try:
                data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
                if (data.get("v") != 2 or data.get("q") != _query_digest(normalized)
                        or not isinstance(data.get("o"), str) or type(data.get("s")) is not int
                        or not 0 <= data["s"] <= RAW_PAGE_SIZE):
                    raise ValueError
                offset, skip = data["o"], data["s"]
            except (ValueError, TypeError, AttributeError, binascii.Error) as exc:
                raise CursorError("Invalid search cursor; restart the search after upgrading") from exc
        policy = Policy.load(self.profile)
        with self._operation_lock:
            deadline = time.monotonic() + OPERATION_TIMEOUT
            session = self._ready(policy, timeout=_remaining(deadline, 30.0))
            api = TdApi(TdlibSchema.CURRENT)
            titles, items, visited = {}, [], set()
            next_cursor = None
            for _ in range(MAX_RAW_PAGES):
                if offset in visited:
                    next_cursor = None
                    break
                visited.add(offset)
                next_cursor = None
                response = session.request(api.search_messages(normalized, cursor=GlobalSearchCursor(offset=offset), limit=RAW_PAGE_SIZE), timeout=_remaining(deadline, 15.0))
                page = api.parse_search_messages(response)
                next_offset = page.next_cursor.offset if page.next_cursor else ""
                for index, message in enumerate(page.messages):
                    if index < skip:
                        continue
                    item = _raw_message(session, message, titles, deadline=deadline)
                    if item is not None:
                        items.append(item)
                    if len(items) == limit:
                        if index + 1 < len(page.messages):
                            next_cursor = _modern_cursor(normalized, offset, index + 1)
                        elif next_offset and next_offset != offset:
                            next_cursor = _modern_cursor(normalized, next_offset, 0)
                        break
                if len(items) == limit or not next_offset or next_offset == offset:
                    break
                offset, skip = next_offset, 0
                next_cursor = _modern_cursor(normalized, offset, 0)
            self._verify_profile(policy, session)
            return RawMessagePage(tuple(items), next_cursor)

    def _get_message_sync(self, *, chat_id: int, message_id: int) -> RawMessage | None:
        policy = Policy.load(self.profile)
        with self._operation_lock:
            deadline = time.monotonic() + OPERATION_TIMEOUT
            session = self._ready(policy, timeout=_remaining(deadline, 30.0))
            chat = session.get_chat(chat_id, timeout=_remaining(deadline, 10.0))
            _reject_secret_chat(chat)
            try:
                message = session.request(
                    TdApi.get_message(chat_id, message_id),
                    timeout=_remaining(deadline, 20.0),
                )
            except TdlibError as exc:
                if exc.code == 400:
                    return None
                raise
            if int(message.get("chat_id", 0)) != int(chat_id):
                return None
            self._verify_profile(policy, session)
            return _raw_message(
                session,
                message,
                {int(chat_id): str(chat.get("title", ""))},
                deadline=deadline,
            )

    def _get_context_sync(
        self, *, chat_id: int, message_id: int, before: int, after: int
    ) -> Sequence[RawMessage]:
        policy = Policy.load(self.profile)
        with self._operation_lock:
            deadline = time.monotonic() + OPERATION_TIMEOUT
            session = self._ready(policy, timeout=_remaining(deadline, 30.0))
            chat = session.get_chat(chat_id, timeout=_remaining(deadline, 10.0))
            _reject_secret_chat(chat)
            response = session.request(
                TdApi.get_message_context(
                    chat_id,
                    message_id,
                    older=before,
                    newer=after,
                    only_local=False,
                ),
                timeout=_remaining(deadline, 20.0),
            )
            title_cache = {int(chat_id): str(chat.get("title", ""))}
            parsed = [
                item
                for message in (response.get("messages") or ())
                if isinstance(message, dict)
                and (
                    item := _raw_message(
                        session, message, title_cache, deadline=deadline
                    )
                )
                is not None
            ]
            parsed.sort(key=lambda item: (item.sent_at, item.chat_id, item.message_id))
            self._verify_profile(policy, session)
            return tuple(parsed)

    def _get_media_sync(
        self,
        *,
        chat_id: int,
        message_id: int,
        quality: MediaQuality,
        max_bytes: int,
    ) -> RawMedia | None:
        if quality not in ("preview", "full"):
            raise ValueError("quality must be 'preview' or 'full'")
        if not 1 <= max_bytes <= HARD_MAX_MEDIA_BYTES:
            raise ValueError("max_bytes exceeds the fixed media safety limit")

        policy = Policy.load(self.profile)
        with self._operation_lock:
            deadline = time.monotonic() + OPERATION_TIMEOUT
            session = self._ready(policy, timeout=_remaining(deadline, 30.0))
            chat = session.get_chat(chat_id, timeout=_remaining(deadline, 10.0))
            _reject_secret_chat(chat)
            try:
                message = session.request(
                    TdApi.get_message(chat_id, message_id),
                    timeout=_remaining(deadline, 15.0),
                )
            except TdlibError as exc:
                if exc.code == 400:
                    return None
                raise
            if int(message.get("chat_id", 0)) != int(chat_id):
                return None

            if message.get("can_be_saved") is False:
                raise MediaError("Telegram marks this message as protected from saving")
            if int(message.get("ttl", 0) or 0) > 0 or float(
                message.get("ttl_expires_in", 0) or 0
            ) > 0 or message.get("self_destruct_type") or message.get("self_destruct_in", 0):
                raise MediaError("Self-destructing Telegram media is not available")

            content = message.get("content")
            if isinstance(content, dict) and content.get("is_secret") is True:
                raise MediaError("Secret Telegram media is not available")
            candidate = _select_media_candidate(
                content,
                quality=quality,
                max_bytes=max_bytes,
            )
            if candidate is None:
                return None

            declared_size = _declared_file_size(candidate.file)
            if declared_size > max_bytes:
                raise MediaTooLargeError(
                    f"Telegram media is {declared_size} bytes; the {quality} limit is "
                    f"{max_bytes} bytes. Request a preview or use Telegram directly."
                )
            file_id = int(candidate.file.get("id", 0))
            if file_id <= 0:
                return None
            downloaded = session.request(
                TdApi.download_file(
                    file_id,
                    priority=32,
                    limit=max_bytes + 1,
                    synchronous=True,
                ),
                timeout=_remaining(deadline, 30.0),
            )
            local = downloaded.get("local") or {}
            if not local.get("is_downloading_completed"):
                downloaded_size = int(local.get("downloaded_size", 0))
                if (
                    downloaded_size > max_bytes
                    or _declared_file_size(downloaded) > max_bytes
                ):
                    raise MediaTooLargeError(
                        f"Telegram media exceeds the {max_bytes}-byte {quality} limit."
                    )
                raise MediaError("Telegram did not finish the bounded media download")
            path = str(local.get("path", ""))
            data = _read_private_media_file(
                path,
                root=files_dir(self.profile),
                max_bytes=max_bytes,
            )
            mime_type = _verified_media_mime(data, candidate.mime_type)
            width, height = candidate.width, candidate.height
            if candidate.media_kind == "image":
                width, height = _verified_image_dimensions(data, mime_type)
            self._verify_profile(policy, session)
            return RawMedia(
                chat_id=int(chat_id),
                message_id=int(message_id),
                content_type=str((content or {}).get("@type", ""))[:80],
                media_kind=candidate.media_kind,
                mime_type=mime_type,
                data=data,
                file_name=_safe_file_name(candidate.file_name),
                width=width,
                height=height,
                is_preview=candidate.is_preview,
            )

    def _verify_profile(self, previous: Policy, session: TdlibSession) -> None:
        current = Policy.load(self.profile)
        if (
            current.api_id != previous.api_id
            or current.expected_user_id != previous.expected_user_id
            or current.expected_user_id != session.user_id
        ):
            raise PolicyError("Telegram profile changed during the request")


def _modern_cursor(query: str, offset: str, skip: int) -> str:
    payload = {"v": 2, "q": _query_digest(query), "o": offset, "s": skip}
    value = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    if len(value) > 512:
        raise CursorError("Telegram returned an oversized search cursor")
    return value


def encode_search_cursor(cursor: GlobalSearchCursor, query: str) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "q": _query_digest(query),
        "d": int(cursor.offset_date),
        "c": int(cursor.offset_chat_id),
        "m": int(cursor.offset_message_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_search_cursor(value: str | None, query: str) -> GlobalSearchCursor:
    if value is None:
        return GlobalSearchCursor()
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("v") != CURSOR_VERSION or payload.get("q") != _query_digest(query):
            raise ValueError
        cursor = GlobalSearchCursor(
            offset_date=int(payload["d"]),
            offset_chat_id=int(payload["c"]),
            offset_message_id=int(payload["m"]),
        )
    except (UnicodeError, binascii.Error, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise CursorError("Invalid or query-mismatched Telegram search cursor") from exc
    if cursor.offset_date < 0 or cursor.offset_message_id < 0:
        raise CursorError("Invalid Telegram search cursor values")
    return cursor


def _query_digest(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:20]


def _cursor_from_message(message: dict[str, Any]) -> GlobalSearchCursor:
    return GlobalSearchCursor(
        offset_date=int(message.get("date", 0)),
        offset_chat_id=int(message.get("chat_id", 0)),
        offset_message_id=int(message.get("id", 0)),
    )


def _cursor_tuple(cursor: GlobalSearchCursor) -> tuple[int, int, int]:
    return (
        int(cursor.offset_date),
        int(cursor.offset_chat_id),
        int(cursor.offset_message_id),
    )


def _decode_database_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SetupRequiredError("Stored TDLib database key is invalid") from exc
    if len(decoded) != 32:
        raise SetupRequiredError("Stored TDLib database key has an invalid length")
    return decoded


def encode_database_key(value: bytes) -> str:
    if len(value) != 32:
        raise ValueError("TDLib database key must be exactly 32 bytes")
    return base64.b64encode(value).decode("ascii")


def _raw_message(
    session: TdlibSession,
    message: dict[str, Any],
    title_cache: dict[int, str],
    *,
    deadline: float | None = None,
) -> RawMessage | None:
    chat_id = int(message.get("chat_id", 0))
    message_id = int(message.get("id", 0))
    timestamp = int(message.get("date", 0))
    extracted = _extract_text(message.get("content"))
    if chat_id == 0 or message_id <= 0 or timestamp <= 0 or extracted is None:
        return None
    text, content_type = extracted
    if chat_id not in title_cache:
        timeout = _remaining(deadline, 5.0) if deadline is not None else 5.0
        chat = session.get_chat(chat_id, timeout=timeout)
        _reject_secret_chat(chat)
        title_cache[chat_id] = str(chat.get("title", ""))
    sender = message.get("sender_id") or {}
    sender_id = (
        int(sender.get("user_id", 0))
        if sender.get("@type") == "messageSenderUser"
        else None
    )
    return RawMessage(
        chat_id=chat_id,
        chat_title=title_cache[chat_id],
        message_id=message_id,
        sender_id=sender_id or None,
        sent_at=datetime.fromtimestamp(timestamp, timezone.utc),
        text=text,
        content_type=content_type,
    )


def _extract_text(content: Any) -> tuple[str, str] | None:
    if not isinstance(content, dict):
        return None
    content_type = str(content.get("@type", ""))
    if content_type in {"messageVoiceNote", "messageVideoNote"}:
        key = "voice_note" if content_type == "messageVoiceNote" else "video_note"
        media = content.get(key) or {}
        result = media.get("speech_recognition_result") or {}
        if result.get("@type") == "speechRecognitionResultText" and isinstance(result.get("text"), str):
            return result["text"], content_type
        caption = (content.get("caption") or {}).get("text")
        if isinstance(caption, str) and caption:
            return caption, content_type
        return ("[Voice note]" if key == "voice_note" else "[Video note]"), content_type
    formatted = content.get("text") if content_type == "messageText" else content.get("caption")
    if not isinstance(formatted, dict):
        return None
    text = formatted.get("text")
    if not isinstance(text, str) or not text:
        return None
    return text, content_type


def _select_media_candidate(
    content: Any,
    *,
    quality: MediaQuality,
    max_bytes: int,
) -> _MediaCandidate | None:
    if not isinstance(content, dict):
        return None
    content_type = str(content.get("@type", ""))

    if content_type == "messagePhoto":
        sizes = (content.get("photo") or {}).get("sizes") or ()
        candidates = [
            _MediaCandidate(
                file=size.get("photo") or {},
                media_kind="image",
                mime_type="image/jpeg",
                width=_positive_int(size.get("width")),
                height=_positive_int(size.get("height")),
                is_preview=quality == "preview",
            )
            for size in sizes
            if isinstance(size, dict)
            and int((size.get("photo") or {}).get("id", 0)) > 0
        ]
        candidates.sort(
            key=lambda item: (item.width or 0) * (item.height or 0), reverse=True
        )
        if quality == "full":
            return candidates[0] if candidates else None
        known_fitting = next(
            (
                item
                for item in candidates
                if 0 < _declared_file_size(item.file) <= max_bytes
            ),
            None,
        )
        if known_fitting is not None:
            return known_fitting
        unknown_sizes = [
            item for item in candidates if _declared_file_size(item.file) == 0
        ]
        if unknown_sizes:
            return unknown_sizes[-1]
        return candidates[-1] if candidates else None

    if content_type == "messageDocument":
        document = content.get("document") or {}
        mime_type = _normalized_mime(document.get("mime_type"), document.get("file_name"))
        if quality == "preview":
            preview = _thumbnail_candidate(document.get("thumbnail"))
            if preview is not None:
                return preview
        if mime_type == "application/pdf":
            return _file_candidate(
                document.get("document"),
                media_kind="file",
                mime_type=mime_type,
                file_name=document.get("file_name"),
            )
        return None

    if content_type == "messageAudio":
        audio = content.get("audio") or {}
        mime_type = _normalized_mime(audio.get("mime_type"), audio.get("file_name"))
        if mime_type not in _AUDIO_MIME_TYPES:
            return None
        return _file_candidate(
            audio.get("audio"),
            media_kind="audio",
            mime_type=mime_type,
            file_name=audio.get("file_name"),
        )

    if content_type == "messageVoiceNote":
        voice = content.get("voice_note") or {}
        mime_type = _normalized_mime(voice.get("mime_type"), "voice.ogg")
        if mime_type not in _AUDIO_MIME_TYPES:
            return None
        return _file_candidate(
            voice.get("voice"),
            media_kind="audio",
            mime_type=mime_type,
            file_name="voice.ogg",
        )

    if content_type in {"messageAnimation", "messageVideo", "messageVideoNote"}:
        object_name = {
            "messageAnimation": "animation",
            "messageVideo": "video",
            "messageVideoNote": "video_note",
        }[content_type]
        return _thumbnail_candidate((content.get(object_name) or {}).get("thumbnail"))

    return None


def _file_candidate(
    file: Any,
    *,
    media_kind: MediaKind,
    mime_type: str,
    file_name: Any = None,
) -> _MediaCandidate | None:
    if not isinstance(file, dict) or int(file.get("id", 0)) <= 0:
        return None
    return _MediaCandidate(
        file=file,
        media_kind=media_kind,
        mime_type=mime_type,
        file_name=str(file_name) if file_name else None,
    )


def _thumbnail_candidate(thumbnail: Any) -> _MediaCandidate | None:
    if not isinstance(thumbnail, dict):
        return None
    formats = {
        "thumbnailFormatJpeg": "image/jpeg",
        "thumbnailFormatPng": "image/png",
        "thumbnailFormatWebp": "image/webp",
    }
    mime_type = formats.get(str((thumbnail.get("format") or {}).get("@type", "")))
    file = thumbnail.get("file")
    if mime_type is None or not isinstance(file, dict) or int(file.get("id", 0)) <= 0:
        return None
    return _MediaCandidate(
        file=file,
        media_kind="image",
        mime_type=mime_type,
        width=_positive_int(thumbnail.get("width")),
        height=_positive_int(thumbnail.get("height")),
        is_preview=True,
    )


def _declared_file_size(file: Any) -> int:
    if not isinstance(file, dict):
        return 0
    return max(
        int(file.get("size", 0) or 0),
        int(file.get("expected_size", 0) or 0),
    )


def _normalized_mime(value: Any, file_name: Any = None) -> str:
    candidate = str(value or "").split(";", 1)[0].strip().lower()
    if candidate:
        return candidate
    guessed, _ = mimetypes.guess_type(str(file_name or ""))
    return str(guessed or "application/octet-stream").lower()


def _verified_media_mime(data: bytes, claimed: str) -> str:
    checks = {
        "application/pdf": lambda value: value.startswith(b"%PDF-"),
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": lambda value: len(value) >= 12
        and value.startswith(b"RIFF")
        and value[8:12] == b"WEBP",
        "audio/flac": lambda value: value.startswith(b"fLaC"),
        "audio/ogg": lambda value: value.startswith(b"OggS"),
        "audio/wav": lambda value: len(value) >= 12
        and value.startswith(b"RIFF")
        and value[8:12] == b"WAVE",
        "audio/mpeg": lambda value: value.startswith(b"ID3")
        or (len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0),
        "audio/aac": lambda value: len(value) >= 2
        and value[0] == 0xFF
        and value[1] & 0xF0 == 0xF0,
        "audio/mp4": lambda value: len(value) >= 12 and value[4:8] == b"ftyp",
        "audio/m4a": lambda value: len(value) >= 12 and value[4:8] == b"ftyp",
        "audio/x-m4a": lambda value: len(value) >= 12 and value[4:8] == b"ftyp",
    }
    check = checks.get(claimed)
    if check is None or not check(data):
        raise MediaError("Downloaded Telegram media does not match its declared type")
    return claimed


def _verified_image_dimensions(data: bytes, mime_type: str) -> tuple[int, int]:
    if mime_type == "image/jpeg":
        dimensions = _jpeg_dimensions(data)
    elif mime_type == "image/png":
        dimensions = _png_dimensions(data)
    elif mime_type == "image/webp":
        dimensions = _webp_dimensions(data)
    else:
        raise MediaError("Telegram image format is not safe for native rendering")

    width, height = dimensions
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise MediaError("Telegram image dimensions exceed the safe rendering limit")
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA or offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in start_of_frame and segment_length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    raise MediaError("Telegram JPEG has no valid image dimensions")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise MediaError("Telegram PNG has no valid IHDR header")
    if b"acTL" in data:
        raise MediaError("Animated PNG is not available for native rendering")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        raise MediaError("Telegram WebP has no valid image header")
    chunk_type = data[12:16]
    payload = data[20:]
    if chunk_type == b"VP8X" and len(payload) >= 10:
        if payload[0] & 0x02:
            raise MediaError("Animated WebP is not available for native rendering")
        width = 1 + int.from_bytes(payload[4:7], "little")
        height = 1 + int.from_bytes(payload[7:10], "little")
        return width, height
    if chunk_type == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
        width = int.from_bytes(payload[6:8], "little") & 0x3FFF
        height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        return width, height
    if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        width = 1 + payload[1] + ((payload[2] & 0x3F) << 8)
        height = 1 + (payload[2] >> 6) + (payload[3] << 2) + (
            (payload[4] & 0x0F) << 10
        )
        return width, height
    raise MediaError("Telegram WebP has no supported image dimensions")


def _positive_int(value: Any) -> int | None:
    candidate = int(value or 0)
    return candidate if candidate > 0 else None


def _safe_file_name(value: str | None) -> str | None:
    if not value:
        return None
    name = Path(value.replace("\x00", "")).name.strip()
    return name[:255] or None


def _read_private_media_file(path_value: str, *, root: Path, max_bytes: int) -> bytes:
    if not path_value:
        raise MediaError("TDLib returned no local path for downloaded media")
    safe_root = root.resolve(strict=True)
    candidate = Path(path_value)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(safe_root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise MediaError("TDLib returned an unsafe media path") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(resolved, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise MediaError("Downloaded Telegram media has unsafe ownership or type")
        if info.st_size <= 0:
            raise MediaError("Downloaded Telegram media is empty")
        if info.st_size > max_bytes:
            raise MediaTooLargeError(
                f"Downloaded Telegram media exceeds the {max_bytes}-byte limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(max_bytes + 1)
        if len(data) != info.st_size or len(data) > max_bytes:
            raise MediaTooLargeError("Telegram media changed size while being read")
        return data
    finally:
        os.close(descriptor)


def _reject_secret_chat(chat: dict[str, Any]) -> None:
    if (chat.get("type") or {}).get("@type") == "chatTypeSecret":
        raise PolicyError("Secret chats are never available to this bridge")


def _remaining(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Telegram operation exceeded its bounded runtime")
    return min(cap, remaining)


__all__ = [
    "CURSOR_VERSION",
    "CursorError",
    "SCHEMA",
    "EXPECTED_TDLIB_VERSION",
    "SessionBusyError",
    "SetupRequiredError",
    "TDLibBackend",
    "TdlibSession",
    "decode_search_cursor",
    "encode_database_key",
    "encode_search_cursor",
]
