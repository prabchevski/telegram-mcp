"""Small, dependency-free Python core for TDLib's official JSON C API.

The module deliberately exposes only authorization and read/search requests.  It
does not expose Telegram write operations.  The linked TDLib user session still
has the account's full technical authority, so an outer MCP layer must enforce
its own chat allow-list.

TDLib has a rolling API.  Homebrew's stable 1.8.0 schema differs materially from
the current upstream schema, so request construction is explicit about which
schema is in use.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import copy
import ctypes
import json
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import __version__


JsonObject = dict[str, Any]
UpdateHandler = Callable[[JsonObject], None]


class TdlibSchema(str, Enum):
    """The two schemas supported by this adapter."""

    CURRENT = "current"
    V1_8 = "1.8"

    @classmethod
    def coerce(cls, value: "TdlibSchema | str") -> "TdlibSchema":
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower()
        aliases = {
            "current": cls.CURRENT,
            "master": cls.CURRENT,
            "head": cls.CURRENT,
            "1.8": cls.V1_8,
            "1.8.0": cls.V1_8,
            "v1.8": cls.V1_8,
            "v1.8.0": cls.V1_8,
            "homebrew": cls.V1_8,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unsupported TDLib schema: {value!r}") from exc


class TdlibError(RuntimeError):
    """An ``error`` object returned by TDLib."""

    def __init__(self, response: Mapping[str, Any]):
        self.response = dict(response)
        self.code = int(response.get("code", 0))
        self.message = str(response.get("message", "unknown TDLib error"))
        super().__init__(f"TDLib error {self.code}: {self.message}")


class TdlibStoppedError(RuntimeError):
    pass


class TdlibProtocolError(RuntimeError):
    pass


def _encoded_bytes(value: bytes) -> str:
    """Encode a TL ``bytes`` field for the JSON API."""

    return base64.b64encode(value).decode("ascii")


@dataclass(frozen=True)
class TdlibParameters:
    api_id: int
    api_hash: str = field(repr=False)
    database_directory: str
    files_directory: str = ""
    database_encryption_key: bytes = field(default=b"", repr=False)
    use_test_dc: bool = False
    use_file_database: bool = True
    use_chat_info_database: bool = True
    use_message_database: bool = True
    use_secret_chats: bool = False
    system_language_code: str = "en"
    device_model: str = "Shared local TDLib client"
    system_version: str = "macOS"
    application_version: str = __version__
    enable_storage_optimizer: bool = True
    ignore_file_names: bool = False

    def __post_init__(self) -> None:
        if self.api_id <= 0:
            raise ValueError("api_id must be a positive integer")
        if not self.api_hash:
            raise ValueError("api_hash must not be empty")
        if not self.database_directory:
            raise ValueError("database_directory must not be empty")
        for name in ("system_language_code", "device_model", "application_version"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")

    def initialization_request(self, schema: TdlibSchema | str) -> JsonObject:
        schema = TdlibSchema.coerce(schema)
        common: JsonObject = {
            "use_test_dc": self.use_test_dc,
            "database_directory": self.database_directory,
            "files_directory": self.files_directory,
            "use_file_database": self.use_file_database,
            "use_chat_info_database": self.use_chat_info_database,
            "use_message_database": self.use_message_database,
            "use_secret_chats": self.use_secret_chats,
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "system_language_code": self.system_language_code,
            "device_model": self.device_model,
            "system_version": self.system_version,
            "application_version": self.application_version,
        }
        if schema is TdlibSchema.CURRENT:
            return {
                "@type": "setTdlibParameters",
                **common,
                "database_encryption_key": _encoded_bytes(
                    self.database_encryption_key
                ),
            }

        # TDLib 1.8.0 wraps fields in tdlibParameters and asks for the
        # database key in a subsequent authorization state.
        legacy_parameters = {
            **common,
            "enable_storage_optimizer": self.enable_storage_optimizer,
            "ignore_file_names": self.ignore_file_names,
        }
        return {"@type": "setTdlibParameters", "parameters": legacy_parameters}

    def legacy_encryption_key_request(self) -> JsonObject:
        return {
            "@type": "checkDatabaseEncryptionKey",
            "encryption_key": _encoded_bytes(self.database_encryption_key),
        }


def _bounded_limit(limit: int, *, maximum: int = 100) -> int:
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _chat_list(name: str | None, *, allow_all: bool) -> JsonObject | None:
    if name is None or name == "all":
        if allow_all:
            return None
        name = "main"
    normalized = name.lower()
    if normalized == "main":
        return {"@type": "chatListMain"}
    if normalized == "archive":
        return {"@type": "chatListArchive"}
    raise ValueError("chat list must be 'main', 'archive', or 'all'")


@dataclass(frozen=True)
class GlobalSearchCursor:
    """Version-neutral cursor; only one side is used by a given schema."""

    offset: str = ""
    offset_date: int = 0
    offset_chat_id: int = 0
    offset_message_id: int = 0


@dataclass(frozen=True)
class MessagePage:
    total_count: int
    messages: tuple[JsonObject, ...]
    next_cursor: GlobalSearchCursor | None


@dataclass(frozen=True)
class ChatMessagePage:
    total_count: int
    messages: tuple[JsonObject, ...]
    next_from_message_id: int


class TdApi:
    """Pure TDLib request builders and response normalizers."""

    def __init__(self, schema: TdlibSchema | str):
        self.schema = TdlibSchema.coerce(schema)

    @staticmethod
    def get_authorization_state() -> JsonObject:
        return {"@type": "getAuthorizationState"}

    @staticmethod
    def close() -> JsonObject:
        return {"@type": "close"}

    @staticmethod
    def get_chat(chat_id: int) -> JsonObject:
        return {"@type": "getChat", "chat_id": int(chat_id)}

    @staticmethod
    def get_message(chat_id: int, message_id: int) -> JsonObject:
        return {
            "@type": "getMessage",
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        }

    @staticmethod
    def download_file(
        file_id: int,
        *,
        priority: int = 32,
        offset: int = 0,
        limit: int = 0,
        synchronous: bool = True,
    ) -> JsonObject:
        if file_id <= 0:
            raise ValueError("file_id must be positive")
        if not 1 <= priority <= 32:
            raise ValueError("priority must be between 1 and 32")
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be non-negative")
        return {
            "@type": "downloadFile",
            "file_id": int(file_id),
            "priority": int(priority),
            "offset": int(offset),
            "limit": int(limit),
            "synchronous": bool(synchronous),
        }

    def load_chats(self, *, chat_list: str = "main", limit: int = 100) -> JsonObject:
        return {
            "@type": "loadChats",
            "chat_list": _chat_list(chat_list, allow_all=False),
            "limit": _bounded_limit(limit),
        }

    def get_chats(self, *, chat_list: str = "main", limit: int = 100) -> JsonObject:
        return {
            "@type": "getChats",
            "chat_list": _chat_list(chat_list, allow_all=False),
            "limit": _bounded_limit(limit),
        }

    def search_chats(self, query: str, *, limit: int = 50) -> JsonObject:
        request: JsonObject = {
            "@type": "searchChats",
            "query": query,
            "limit": _bounded_limit(limit),
        }
        if self.schema is TdlibSchema.CURRENT:
            request["type_filter"] = None
        return request

    def search_messages(
        self,
        query: str,
        *,
        cursor: GlobalSearchCursor | None = None,
        limit: int = 50,
        chat_list: str | None = "all",
        filter_: JsonObject | None = None,
        min_date: int = 0,
        max_date: int = 0,
        chat_type_filter: JsonObject | None = None,
    ) -> JsonObject:
        cursor = cursor or GlobalSearchCursor()
        request: JsonObject = {
            "@type": "searchMessages",
            "chat_list": _chat_list(chat_list, allow_all=True),
            "query": query,
            "limit": _bounded_limit(limit),
            "filter": copy.deepcopy(filter_),
            "min_date": int(min_date),
            "max_date": int(max_date),
        }
        if self.schema is TdlibSchema.CURRENT:
            request["offset"] = cursor.offset
            request["chat_type_filter"] = copy.deepcopy(chat_type_filter)
        else:
            if chat_type_filter is not None:
                raise ValueError("chat_type_filter isn't supported by TDLib 1.8.0")
            request.update(
                offset_date=int(cursor.offset_date),
                offset_chat_id=int(cursor.offset_chat_id),
                offset_message_id=int(cursor.offset_message_id),
            )
        return request

    def parse_search_messages(self, response: Mapping[str, Any]) -> MessagePage:
        _raise_if_error(response)
        messages = tuple(
            dict(item) for item in (response.get("messages") or ()) if item is not None
        )
        total_count = int(response.get("total_count", len(messages)))
        if self.schema is TdlibSchema.CURRENT:
            token = str(response.get("next_offset", ""))
            cursor = GlobalSearchCursor(offset=token) if token else None
        elif messages:
            last = messages[-1]
            cursor = GlobalSearchCursor(
                offset_date=int(last.get("date", 0)),
                offset_chat_id=int(last.get("chat_id", 0)),
                offset_message_id=int(last.get("id", 0)),
            )
        else:
            cursor = None
        return MessagePage(total_count, messages, cursor)

    def search_chat_messages(
        self,
        chat_id: int,
        query: str,
        *,
        from_message_id: int = 0,
        offset: int = 0,
        limit: int = 50,
        sender_id: JsonObject | None = None,
        filter_: JsonObject | None = None,
        topic_id: JsonObject | None = None,
        legacy_message_thread_id: int = 0,
    ) -> JsonObject:
        limit = _bounded_limit(limit)
        if offset < 0 and limit <= -offset:
            raise ValueError("limit must be greater than -offset")
        request: JsonObject = {
            "@type": "searchChatMessages",
            "chat_id": int(chat_id),
            "query": query,
            "sender_id": copy.deepcopy(sender_id),
            "from_message_id": int(from_message_id),
            "offset": int(offset),
            "limit": limit,
            "filter": copy.deepcopy(filter_),
        }
        if self.schema is TdlibSchema.CURRENT:
            if legacy_message_thread_id:
                raise ValueError(
                    "legacy_message_thread_id isn't valid for the current schema"
                )
            request["topic_id"] = copy.deepcopy(topic_id)
        else:
            if topic_id is not None:
                raise ValueError("topic_id isn't supported by TDLib 1.8.0")
            request["message_thread_id"] = int(legacy_message_thread_id)
        return request

    def parse_search_chat_messages(
        self, response: Mapping[str, Any]
    ) -> ChatMessagePage:
        _raise_if_error(response)
        messages = tuple(
            dict(item) for item in (response.get("messages") or ()) if item is not None
        )
        total_count = int(response.get("total_count", len(messages)))
        if self.schema is TdlibSchema.CURRENT:
            next_id = int(response.get("next_from_message_id", 0))
        else:
            next_id = int(messages[-1].get("id", 0)) if messages else 0
        return ChatMessagePage(total_count, messages, next_id)

    @staticmethod
    def get_chat_history(
        chat_id: int,
        *,
        from_message_id: int = 0,
        offset: int = 0,
        limit: int = 50,
        only_local: bool = False,
    ) -> JsonObject:
        limit = _bounded_limit(limit)
        if not -99 <= offset <= 0:
            raise ValueError("offset must be between -99 and 0")
        if offset < 0 and limit < -offset:
            raise ValueError("limit must be at least -offset")
        return {
            "@type": "getChatHistory",
            "chat_id": int(chat_id),
            "from_message_id": int(from_message_id),
            "offset": int(offset),
            "limit": limit,
            "only_local": bool(only_local),
        }

    @staticmethod
    def get_message_context(
        chat_id: int,
        message_id: int,
        *,
        older: int = 5,
        newer: int = 5,
        only_local: bool = False,
    ) -> JsonObject:
        """Build one history request around a target message.

        TDLib returns messages in reverse chronological order.  A negative
        offset asks for newer messages before returning the target and older
        messages.
        """

        if older < 0 or newer < 0:
            raise ValueError("older and newer must be non-negative")
        if newer > 99:
            raise ValueError("newer can't exceed 99")
        limit = older + newer + 1
        if limit > 100:
            raise ValueError("the context window can't exceed 100 messages")
        return TdApi.get_chat_history(
            chat_id,
            from_message_id=message_id,
            offset=-newer,
            limit=limit,
            only_local=only_local,
        )


def _raise_if_error(response: Mapping[str, Any]) -> None:
    if response.get("@type") == "error":
        raise TdlibError(response)


class JsonTransport(Protocol):
    client_id: int

    def send(self, request: Mapping[str, Any]) -> None: ...

    def receive(self, timeout: float) -> JsonObject | None: ...

    def execute(self, request: Mapping[str, Any]) -> JsonObject: ...


def tdjson_library_candidates(explicit_path: str | os.PathLike[str] | None = None) -> tuple[Path, ...]:
    """Return ordered macOS/Linux candidates without loading anything."""

    values: list[str] = []
    if explicit_path:
        values.append(os.fspath(explicit_path))
    # Do not accept TDJSON_LIBRARY from the ambient environment. Gemini CLI can
    # inherit workspace .env values, and loading a library named there would
    # let a project redirect this local server to arbitrary native code.
    values.extend(
        [
            "/opt/homebrew/opt/tdlib/lib/libtdjson.dylib",
            "/usr/local/opt/tdlib/lib/libtdjson.dylib",
        ]
    )
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        key = os.fspath(value)
        if key not in seen:
            result.append(Path(value))
            seen.add(key)
    return tuple(result)


def infer_schema_from_library_path(path: str | os.PathLike[str]) -> TdlibSchema | None:
    """Best-effort hint only; callers should prefer explicit configuration."""

    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = os.fspath(path)
    if re.search(r"(?:^|[/.-])1\.8(?:\.0)?(?:[/.-]|$)", resolved):
        return TdlibSchema.V1_8
    return None


class CtypesTdJsonTransport:
    """ctypes binding to the modern single-process TDLib JSON C functions.

    ``td_receive`` is global and may not be called concurrently.  This wrapper
    therefore expects one transport/client in the Python process.
    """

    def __init__(
        self,
        library_path: str | os.PathLike[str] | None = None,
        *,
        log_verbosity: int | None = 1,
    ):
        self.library_path, self._library = self._load_library(library_path)
        self._bind()
        if log_verbosity is not None:
            if not 0 <= log_verbosity <= 1023:
                raise ValueError("log_verbosity must be between 0 and 1023")
            result = self.execute(
                {
                    "@type": "setLogVerbosityLevel",
                    "new_verbosity_level": int(log_verbosity),
                }
            )
            _raise_if_error(result)
        self.client_id = int(self._library.td_create_client_id())
        if self.client_id <= 0:
            raise RuntimeError("td_create_client_id returned an invalid client id")

    @staticmethod
    def _load_library(
        explicit_path: str | os.PathLike[str] | None,
    ) -> tuple[Path, ctypes.CDLL]:
        errors: list[str] = []
        for candidate in tdjson_library_candidates(explicit_path):
            try:
                library = ctypes.CDLL(os.fspath(candidate))
                try:
                    resolved = candidate.resolve()
                except OSError:
                    resolved = candidate
                return resolved, library
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
        detail = "\n".join(errors)
        raise FileNotFoundError(f"unable to load libtdjson; tried:\n{detail}")

    def _bind(self) -> None:
        self._library.td_create_client_id.argtypes = []
        self._library.td_create_client_id.restype = ctypes.c_int
        self._library.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self._library.td_send.restype = None
        self._library.td_receive.argtypes = [ctypes.c_double]
        self._library.td_receive.restype = ctypes.c_char_p
        self._library.td_execute.argtypes = [ctypes.c_char_p]
        self._library.td_execute.restype = ctypes.c_char_p

    @staticmethod
    def _encode(request: Mapping[str, Any]) -> bytes:
        return json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _decode(raw: bytes | None) -> JsonObject | None:
        if raw is None:
            return None
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TdlibProtocolError("TDLib returned a non-object JSON value")
        return decoded

    def send(self, request: Mapping[str, Any]) -> None:
        self._library.td_send(self.client_id, self._encode(request))

    def receive(self, timeout: float) -> JsonObject | None:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        return self._decode(self._library.td_receive(float(timeout)))

    def execute(self, request: Mapping[str, Any]) -> JsonObject:
        result = self._decode(self._library.td_execute(self._encode(request)))
        if result is None:
            raise TdlibProtocolError("td_execute unexpectedly returned NULL")
        return result


class TdClient:
    """Correlate asynchronous TDLib responses with ``Future`` objects."""

    def __init__(self, transport: JsonTransport, *, receive_timeout: float = 0.25):
        if receive_timeout <= 0:
            raise ValueError("receive_timeout must be positive")
        self.transport = transport
        self.receive_timeout = receive_timeout
        self._pending: dict[str, Future[JsonObject]] = {}
        self._future_ids: dict[Future[JsonObject], str] = {}
        self._abandoned: OrderedDict[str, None] = OrderedDict()
        self._pending_lock = threading.Lock()
        self._handlers: list[UpdateHandler] = []
        self._handlers_lock = threading.Lock()
        self.updates: queue.Queue[JsonObject] = queue.Queue()
        self.handler_errors: queue.Queue[BaseException] = queue.Queue()
        self._running = threading.Event()
        self._receiver_thread: threading.Thread | None = None
        self._fatal_error: BaseException | None = None

    @property
    def client_id(self) -> int:
        return self.transport.client_id

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def add_update_handler(self, handler: UpdateHandler) -> None:
        with self._handlers_lock:
            self._handlers.append(handler)

    def remove_update_handler(self, handler: UpdateHandler) -> None:
        with self._handlers_lock:
            self._handlers.remove(handler)

    def send(self, request: Mapping[str, Any]) -> Future[JsonObject]:
        if self._fatal_error is not None:
            future: Future[JsonObject] = Future()
            future.set_exception(self._fatal_error)
            return future
        outgoing = copy.deepcopy(dict(request))
        if "@type" not in outgoing:
            raise ValueError("TDLib request is missing @type")
        if "@extra" in outgoing:
            raise ValueError("@extra is reserved for TdClient request correlation")
        correlation_id = f"py:{uuid.uuid4().hex}"
        outgoing["@extra"] = correlation_id
        future = Future()
        with self._pending_lock:
            self._pending[correlation_id] = future
            self._future_ids[future] = correlation_id
        try:
            self.transport.send(outgoing)
        except BaseException as exc:
            with self._pending_lock:
                self._pending.pop(correlation_id, None)
                self._future_ids.pop(future, None)
            future.set_exception(exc)
        return future

    def request(self, request: Mapping[str, Any], *, timeout: float = 30.0) -> JsonObject:
        future = self.send(request)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            if future.done():
                return future.result()
            abandoned_by_caller = False
            with self._pending_lock:
                correlation_id = self._future_ids.pop(future, None)
                if correlation_id is not None and self._pending.get(correlation_id) is future:
                    self._pending.pop(correlation_id, None)
                    self._abandoned[correlation_id] = None
                    abandoned_by_caller = True
                    while len(self._abandoned) > 4096:
                        self._abandoned.popitem(last=False)
            if abandoned_by_caller:
                future.cancel()
                raise
            # The receive thread already claimed this response and marked the
            # Future as running. Let its immediate delivery finish instead of
            # cancelling between map removal and set_result/set_exception.
            return future.result()

    def start(self) -> None:
        if self._receiver_thread and self._receiver_thread.is_alive():
            return
        self._running.set()
        self._receiver_thread = threading.Thread(
            target=self._receive_loop,
            name=f"tdlib-receiver-{self.client_id}",
            daemon=True,
        )
        self._receiver_thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._running.clear()
        if self._receiver_thread and self._receiver_thread is not threading.current_thread():
            self._receiver_thread.join(join_timeout)
        self._fail_pending(TdlibStoppedError("TDLib receiver stopped"))

    def _receive_loop(self) -> None:
        try:
            while self._running.is_set():
                self.process_one(timeout=self.receive_timeout)
        except BaseException as exc:
            self._fatal_error = exc
            self._running.clear()
            self._fail_pending(exc)

    def process_one(self, *, timeout: float = 0.0) -> bool:
        incoming = self.transport.receive(timeout)
        if incoming is None:
            return False
        incoming_client_id = incoming.get("@client_id")
        if incoming_client_id is not None and int(incoming_client_id) != self.client_id:
            # A second modern TDLib JSON client in this process would need one
            # shared td_receive dispatcher.  This deliberately small wrapper
            # supports one client only.
            raise TdlibProtocolError(
                f"received object for unexpected client {incoming_client_id}"
            )
        correlation_id = incoming.get("@extra")
        if isinstance(correlation_id, str):
            deliver = False
            with self._pending_lock:
                future = self._pending.pop(correlation_id, None)
                if future is not None:
                    self._future_ids.pop(future, None)
                    deliver = future.set_running_or_notify_cancel()
                abandoned = correlation_id in self._abandoned
                if abandoned:
                    self._abandoned.pop(correlation_id, None)
            if future is None:
                if abandoned:
                    return True
                raise TdlibProtocolError(
                    f"response has unknown @extra value {correlation_id!r}"
                )
            if not deliver:
                return True
            if incoming.get("@type") == "error":
                future.set_exception(TdlibError(incoming))
            else:
                future.set_result(incoming)
            return True

        self.updates.put(incoming)
        with self._handlers_lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(incoming)
            except BaseException as exc:
                self.handler_errors.put(exc)
        return True

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._future_ids.clear()
            self._abandoned.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)


class AuthorizationMachine:
    """Pure authorization-state reducer and request builder."""

    def __init__(self, parameters: TdlibParameters, schema: TdlibSchema | str):
        self.parameters = parameters
        self.schema = TdlibSchema.coerce(schema)
        self.state: JsonObject | None = None
        self.state_type: str | None = None

    def consume(self, update_or_state: Mapping[str, Any]) -> tuple[JsonObject, ...]:
        object_type = update_or_state.get("@type")
        if object_type == "updateAuthorizationState":
            state = update_or_state.get("authorization_state")
            if not isinstance(state, Mapping):
                raise TdlibProtocolError("authorization update has no state object")
        elif isinstance(object_type, str) and object_type.startswith("authorizationState"):
            state = update_or_state
        else:
            return ()

        self.state = copy.deepcopy(dict(state))
        self.state_type = str(state.get("@type"))
        if self.state_type == "authorizationStateWaitTdlibParameters":
            return (self.parameters.initialization_request(self.schema),)
        if self.state_type == "authorizationStateWaitEncryptionKey":
            if self.schema is not TdlibSchema.V1_8:
                raise TdlibProtocolError(
                    "current schema unexpectedly requested a legacy encryption key"
                )
            return (self.parameters.legacy_encryption_key_request(),)
        return ()

    def request_qr_code(self, other_user_ids: Sequence[int] = ()) -> JsonObject:
        return {
            "@type": "requestQrCodeAuthentication",
            "other_user_ids": [int(value) for value in other_user_ids],
        }

    def submit_phone_number(self, phone_number: str) -> JsonObject:
        if not phone_number:
            raise ValueError("phone_number must not be empty")
        return {
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": phone_number,
            "settings": None,
        }

    @staticmethod
    def submit_code(code: str) -> JsonObject:
        if not code:
            raise ValueError("code must not be empty")
        return {"@type": "checkAuthenticationCode", "code": code}

    @staticmethod
    def submit_password(password: str) -> JsonObject:
        if not password:
            raise ValueError("password must not be empty")
        return {"@type": "checkAuthenticationPassword", "password": password}

    def submit_email_address(self, email_address: str) -> JsonObject:
        if self.schema is not TdlibSchema.CURRENT:
            raise ValueError("email authentication isn't supported by TDLib 1.8.0")
        if not email_address:
            raise ValueError("email_address must not be empty")
        return {
            "@type": "setAuthenticationEmailAddress",
            "email_address": email_address,
        }

    def submit_email_code(self, code: str) -> JsonObject:
        if self.schema is not TdlibSchema.CURRENT:
            raise ValueError("email authentication isn't supported by TDLib 1.8.0")
        if not code:
            raise ValueError("code must not be empty")
        return {
            "@type": "checkAuthenticationEmailCode",
            "code": {"@type": "emailAddressAuthenticationCode", "code": code},
        }

    def register_user(self, first_name: str, last_name: str = "") -> JsonObject:
        if not first_name:
            raise ValueError("first_name must not be empty")
        request: JsonObject = {
            "@type": "registerUser",
            "first_name": first_name,
            "last_name": last_name,
        }
        if self.schema is TdlibSchema.CURRENT:
            request["disable_notification"] = False
        return request


class AuthorizationController:
    """Attach an ``AuthorizationMachine`` to a running ``TdClient``."""

    def __init__(self, client: TdClient, machine: AuthorizationMachine):
        self.client = client
        self.machine = machine
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.automatic_errors: queue.Queue[BaseException] = queue.Queue()
        self._automatic_stages: set[str] = set()
        self._automatic_stages_lock = threading.Lock()
        client.add_update_handler(self._on_update)

    @property
    def state(self) -> JsonObject | None:
        return copy.deepcopy(self.machine.state)

    def begin(self) -> Future[JsonObject]:
        """Start receiving and issue the first request that activates TDLib."""

        future = self.client.send(TdApi.get_authorization_state())
        future.add_done_callback(self._on_bootstrap_response)
        self.client.start()
        return future

    def _on_update(self, update: JsonObject) -> None:
        self._consume_authorization(update)

    def _on_bootstrap_response(self, future: Future[JsonObject]) -> None:
        try:
            response = future.result()
        except BaseException as exc:
            self.automatic_errors.put(exc)
            return
        self._consume_authorization(response)

    def _consume_authorization(self, update_or_state: Mapping[str, Any]) -> None:
        requests = self.machine.consume(update_or_state)
        if self.machine.state_type == "authorizationStateReady":
            self.ready.set()
        elif self.machine.state_type == "authorizationStateClosed":
            self.closed.set()
        for request in requests:
            stage = str(request.get("@type", ""))
            with self._automatic_stages_lock:
                if stage in self._automatic_stages:
                    continue
                self._automatic_stages.add(stage)
            future = self.client.send(request)
            future.add_done_callback(
                lambda completed, current_stage=stage: self._finish_automatic_stage(
                    current_stage, completed
                )
            )

    def _finish_automatic_stage(
        self, stage: str, future: Future[JsonObject]
    ) -> None:
        try:
            future.result()
        except BaseException as exc:
            # A failed request leaves TDLib in the same authorization state.
            # Allow a repeated state (or an explicit retry) to submit it again.
            with self._automatic_stages_lock:
                self._automatic_stages.discard(stage)
            self.automatic_errors.put(exc)

    def send_qr_code_request(
        self, other_user_ids: Sequence[int] = ()
    ) -> Future[JsonObject]:
        return self.client.send(self.machine.request_qr_code(other_user_ids))

    def send_phone_number(self, phone_number: str) -> Future[JsonObject]:
        return self.client.send(self.machine.submit_phone_number(phone_number))

    def send_code(self, code: str) -> Future[JsonObject]:
        return self.client.send(self.machine.submit_code(code))

    def send_password(self, password: str) -> Future[JsonObject]:
        return self.client.send(self.machine.submit_password(password))

    def send_email_address(self, email_address: str) -> Future[JsonObject]:
        return self.client.send(self.machine.submit_email_address(email_address))

    def send_email_code(self, code: str) -> Future[JsonObject]:
        return self.client.send(self.machine.submit_email_code(code))


@dataclass(frozen=True)
class ContextWindow:
    chat_id: int
    target_message_id: int
    messages: tuple[JsonObject, ...]


class ReadOnlyTdlib:
    """Small high-level read/search facade used by the eventual MCP server."""

    def __init__(self, client: TdClient, schema: TdlibSchema | str):
        self.client = client
        self.api = TdApi(schema)

    def load_chats(
        self, *, chat_list: str = "main", limit: int = 100, timeout: float = 30.0
    ) -> JsonObject:
        return self.client.request(
            self.api.load_chats(chat_list=chat_list, limit=limit), timeout=timeout
        )

    def list_chats(
        self, *, chat_list: str = "main", limit: int = 100, timeout: float = 30.0
    ) -> tuple[JsonObject, ...]:
        response = self.client.request(
            self.api.get_chats(chat_list=chat_list, limit=limit), timeout=timeout
        )
        _raise_if_error(response)
        ids = tuple(int(value) for value in response.get("chat_ids", ()))
        futures = [self.client.send(self.api.get_chat(chat_id)) for chat_id in ids]
        return self._collect(futures, timeout)

    def search_chats(
        self, query: str, *, limit: int = 50, timeout: float = 30.0
    ) -> tuple[JsonObject, ...]:
        response = self.client.request(
            self.api.search_chats(query, limit=limit), timeout=timeout
        )
        _raise_if_error(response)
        ids = tuple(int(value) for value in response.get("chat_ids", ()))
        futures = [self.client.send(self.api.get_chat(chat_id)) for chat_id in ids]
        return self._collect(futures, timeout)

    def search_messages(
        self,
        query: str,
        *,
        cursor: GlobalSearchCursor | None = None,
        limit: int = 50,
        chat_list: str | None = "all",
        min_date: int = 0,
        max_date: int = 0,
        timeout: float = 30.0,
    ) -> MessagePage:
        response = self.client.request(
            self.api.search_messages(
                query,
                cursor=cursor,
                limit=limit,
                chat_list=chat_list,
                min_date=min_date,
                max_date=max_date,
            ),
            timeout=timeout,
        )
        return self.api.parse_search_messages(response)

    def search_chat_messages(
        self,
        chat_id: int,
        query: str,
        *,
        from_message_id: int = 0,
        limit: int = 50,
        timeout: float = 30.0,
    ) -> ChatMessagePage:
        response = self.client.request(
            self.api.search_chat_messages(
                chat_id,
                query,
                from_message_id=from_message_id,
                limit=limit,
            ),
            timeout=timeout,
        )
        return self.api.parse_search_chat_messages(response)

    def get_message(
        self, chat_id: int, message_id: int, *, timeout: float = 30.0
    ) -> JsonObject:
        return self.client.request(
            self.api.get_message(chat_id, message_id), timeout=timeout
        )

    def get_context(
        self,
        chat_id: int,
        message_id: int,
        *,
        older: int = 5,
        newer: int = 5,
        only_local: bool = False,
        timeout: float = 30.0,
    ) -> ContextWindow:
        response = self.client.request(
            self.api.get_message_context(
                chat_id,
                message_id,
                older=older,
                newer=newer,
                only_local=only_local,
            ),
            timeout=timeout,
        )
        _raise_if_error(response)
        messages = tuple(
            dict(item) for item in (response.get("messages") or ()) if item is not None
        )
        # A context window is easier for callers to consume chronologically,
        # even though getChatHistory returns reverse chronological order.
        chronological = tuple(
            sorted(messages, key=lambda item: (int(item.get("date", 0)), int(item.get("id", 0))))
        )
        return ContextWindow(int(chat_id), int(message_id), chronological)

    @staticmethod
    def _collect(
        futures: Sequence[Future[JsonObject]], timeout: float
    ) -> tuple[JsonObject, ...]:
        deadline = time.monotonic() + timeout
        results: list[JsonObject] = []
        for future in futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out while resolving chat objects")
            results.append(future.result(timeout=remaining))
        return tuple(results)


__all__ = [
    "AuthorizationController",
    "AuthorizationMachine",
    "ChatMessagePage",
    "ContextWindow",
    "CtypesTdJsonTransport",
    "GlobalSearchCursor",
    "JsonObject",
    "MessagePage",
    "ReadOnlyTdlib",
    "TdApi",
    "TdClient",
    "TdlibError",
    "TdlibParameters",
    "TdlibProtocolError",
    "TdlibSchema",
    "TdlibStoppedError",
    "infer_schema_from_library_path",
    "tdjson_library_candidates",
]
