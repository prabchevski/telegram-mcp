"""Owner-only basic-group upgrades with prepared, durable at-most-once dispatch.

An upgrade creates a different Telegram chat and deactivates the basic group.
The dispatch marker is fsynced before the RPC. An uncertain outcome is reconciled
through the old group's native migration link, never by repeating the upgrade.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from pydantic import Field

from .models import OutputModel, TrustBoundary
from .navigation import ChatId, Id, Request
from .paths import ensure_private_dir
from .platform_support import fsync_directory
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError

UPGRADE_TTL = 300
ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_RECORD_BYTES = 16 * 1024
NOTICE = (
    "Upgrading deactivates the original basic group and creates a supergroup with a new chat ID. "
    "Retain both chat IDs for historical messages and searches. There is no automatic rollback."
)
OperationId = Annotated[str, Field(strict=True, pattern=ID_PATTERN)]
TimeValue = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class GroupUpgradeRequest(Request):
    operation_id: OperationId


class PrepareGroupUpgradeRequest(GroupUpgradeRequest):
    chat_id: ChatId


class GroupUpgradeResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    operation_id: OperationId
    status: Literal["prepared", "upgraded", "already_upgraded", "failed", "unknown"]
    chat_id: ChatId
    basic_group_id: Id
    chat_title: str = Field(max_length=256)
    user_id: Id
    new_chat_id: ChatId | None
    new_supergroup_id: Id | None
    created_at: TimeValue
    expires_at: TimeValue
    dispatched: bool = Field(strict=True)
    observed_at: TimeValue | None
    error: str | None = Field(default=None, max_length=100)
    notice: Literal[NOTICE] = NOTICE


class GroupUpgrades:
    def __init__(self, directory: Path, user_id: int, *,
                 can_dispatch: Callable[[], bool] | None = None) -> None:
        if type(user_id) is not int or not 0 < user_id < 2**53:
            raise ValueError("Invalid Telegram account ID")
        ensure_private_dir(directory)
        self.directory = directory
        self.user_id = user_id
        self.lock = threading.RLock()
        if can_dispatch is not None and not callable(can_dispatch):
            raise ValueError("Group upgrade dispatch policy must be callable")
        self.can_dispatch = can_dispatch if can_dispatch is not None else lambda: True

    def _account(self, session: Any) -> None:
        if type(session.user_id) is not int or session.user_id != self.user_id:
            raise ValueError("Group upgrade belongs to a different Telegram account")

    @staticmethod
    def public(value: dict) -> dict:
        fields = GroupUpgradeResult.model_fields.keys() - {"trust_boundary"}
        return GroupUpgradeResult(**{key: value[key] for key in fields}).model_dump(mode="json")

    def _path(self, operation_id: str) -> Path:
        GroupUpgradeRequest(operation_id=operation_id)
        return self.directory / (operation_id + ".json")

    def _load(self, operation_id: str) -> dict:
        path = self._path(operation_id)
        try:
            _assert_private_file(path)
        except FileNotFoundError:
            raise ValueError("Group upgrade operation not found") from None
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError("Invalid group upgrade record")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.public(value)
            spec = PrepareGroupUpgradeRequest.model_validate(value["spec"]).model_dump()
            if (value["version"] != 1 or type(value["version"]) is not int
                    or value["operation_id"] != operation_id or value["user_id"] != self.user_id
                    or spec != {"operation_id": operation_id, "chat_id": value["chat_id"]}
                    or value["expires_at"] != value["created_at"] + UPGRADE_TTL
                    or (value["new_chat_id"] is None) != (value["new_supergroup_id"] is None)
                    or value["new_chat_id"] == value["chat_id"]
                    or value["status"] == "prepared" and value["dispatched"]
                    or value["status"] == "upgraded" and not value["dispatched"]
                    or value["status"] == "already_upgraded" and value["dispatched"]
                    or value["status"] in {"upgraded", "already_upgraded"}
                    and (value["new_chat_id"] is None or value["observed_at"] is None)):
                raise ValueError("Invalid or differently owned group upgrade record")
            return value
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid group upgrade record") from None

    def _save(self, value: dict) -> None:
        self.public(value)
        if len(json.dumps(value, ensure_ascii=True).encode()) > MAX_RECORD_BYTES:
            raise ValueError("Group upgrade record is too large")
        _atomic_private_json(self._path(value["operation_id"]), value)
        fsync_directory(self.directory)

    @staticmethod
    def _chat(session: Any, chat_id: int) -> tuple[dict, int]:
        chat = session.get_chat(chat_id)
        if (not isinstance(chat, dict) or chat.get("@type") != "chat"
                or type(chat.get("id")) is not int or chat["id"] != chat_id
                or not isinstance(chat.get("type"), dict)
                or chat["type"].get("@type") != "chatTypeBasicGroup"):
            raise ValueError("Only an exact ordinary/basic group can be upgraded")
        basic_id = chat["type"].get("basic_group_id")
        if type(basic_id) is not int or not 0 < basic_id < 2**53:
            raise ValueError("Telegram did not provide the basic group identity")
        return chat, basic_id

    @staticmethod
    def _basic(session: Any, basic_group_id: int) -> dict:
        group = session.request({"@type": "getBasicGroup", "basic_group_id": basic_group_id})
        if (not isinstance(group, dict) or group.get("@type") != "basicGroup"
                or type(group.get("id")) is not int or group["id"] != basic_group_id
                or type(group.get("is_active")) is not bool
                or type(group.get("upgraded_to_supergroup_id")) is not int
                or not 0 <= group["upgraded_to_supergroup_id"] < 2**53):
            raise ValueError("Telegram did not provide exact basic group state")
        return group

    @staticmethod
    def _owner(group: dict) -> None:
        status = group.get("status")
        if (not isinstance(status, dict) or status.get("@type") != "chatMemberStatusCreator"
                or status.get("is_member") is not True or group["is_active"] is not True):
            raise ValueError("Upgrading requires the current account to be the active group's owner")

    @staticmethod
    def _new_chat(chat: Any, *, basic_group_id: int, chat_id: int,
                  supergroup_id: int | None = None) -> tuple[int, int]:
        if (not isinstance(chat, dict) or chat.get("@type") != "chat"
                or type(chat.get("id")) is not int or not -(2**53) < chat["id"] < 2**53
                or chat["id"] in {0, chat_id} or not isinstance(chat.get("type"), dict)
                or chat["type"].get("@type") != "chatTypeSupergroup"
                or chat["type"].get("is_channel") is not False):
            raise ValueError("Telegram did not return a new non-channel supergroup")
        new_id = chat["type"].get("supergroup_id")
        if (type(new_id) is not int or not 0 < new_id < 2**53
                or supergroup_id is not None and new_id != supergroup_id):
            raise ValueError("Telegram returned a different supergroup identity")
        return chat["id"], new_id

    def _observe_migration(self, session: Any, value: dict, group: dict) -> bool:
        new_id = group["upgraded_to_supergroup_id"]
        if not new_id:
            return False
        if group["is_active"] is not False:
            raise ValueError("Basic group migration state is inconsistent")
        # Despite its name, createSupergroupChat only resolves an EXISTING group.
        chat = session.request({"@type": "createSupergroupChat", "supergroup_id": new_id, "force": False})
        chat_id, new_id = self._new_chat(chat, basic_group_id=value["basic_group_id"],
            chat_id=value["chat_id"], supergroup_id=new_id)
        if value["new_chat_id"] is not None and (
                value["new_chat_id"] != chat_id or value["new_supergroup_id"] != new_id):
            raise ValueError("Migration result differs from the recorded native response")
        info = session.request({"@type": "getSupergroupFullInfo", "supergroup_id": new_id})
        if (not isinstance(info, dict) or info.get("@type") != "supergroupFullInfo"
                or type(info.get("upgraded_from_basic_group_id")) is not int
                or info["upgraded_from_basic_group_id"] != value["basic_group_id"]):
            raise ValueError("Telegram did not confirm the original basic group migration link")
        value.update(new_chat_id=chat_id, new_supergroup_id=new_id, observed_at=time.time(),
            status="upgraded" if value["dispatched"] else "already_upgraded", error=None)
        return True

    def _expire(self, value: dict) -> bool:
        if value["status"] == "prepared" and time.time() >= value["expires_at"]:
            value.update(status="failed", error="prepared_upgrade_expired")
            self._save(value)
            return True
        return False

    def _guard_unfinished(self, *, operation_id: str, chat_id: int,
                          basic_group_id: int, limit_new: bool = False) -> None:
        """A new operation ID must never bypass an uncertain target's marker."""
        unfinished = 0
        for entry in self.directory.iterdir():
            if len(entry.name) != 37 or not entry.name.endswith(".json"):
                continue
            try:
                GroupUpgradeRequest(operation_id=entry.stem)
            except ValueError:
                continue
            if entry.stem == operation_id:
                continue
            old = self._load(entry.stem)
            self._expire(old)
            if old["status"] not in {"prepared", "unknown"}:
                continue
            unfinished += 1
            if old["chat_id"] == chat_id or old["basic_group_id"] == basic_group_id:
                raise ValueError(
                    "This group already has an unfinished upgrade; inspect and reuse "
                    f"operation_id {old['operation_id']} instead of preparing another operation"
                )
        if limit_new and unfinished >= 32:
            raise ValueError("Too many unfinished group upgrades; inspect their existing operation IDs")

    def prepare(self, session: Any, *, operation_id: str, chat_id: int) -> dict:
        request = PrepareGroupUpgradeRequest(operation_id=operation_id, chat_id=chat_id)
        self._account(session)
        with self.lock:
            path = self._path(operation_id)
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            else:
                value = self._load(operation_id)
                if value["spec"] != request.model_dump():
                    raise ValueError("operation_id already belongs to a different group")
                self._expire(value)
                return self.public(value)
            chat, basic_id = self._chat(session, chat_id)
            group = self._basic(session, basic_id)
            self._guard_unfinished(operation_id=operation_id, chat_id=chat_id,
                basic_group_id=basic_id, limit_new=True)
            if not group["upgraded_to_supergroup_id"]:
                self._owner(group)
            now = time.time()
            value = {"version": 1, "spec": request.model_dump(), "operation_id": operation_id,
                "status": "prepared", "chat_id": chat_id, "basic_group_id": basic_id,
                "chat_title": str(chat.get("title", ""))[:256], "user_id": self.user_id,
                "new_chat_id": None, "new_supergroup_id": None, "created_at": now,
                "expires_at": now + UPGRADE_TTL, "dispatched": False, "observed_at": now,
                "error": None, "notice": NOTICE}
            if group["upgraded_to_supergroup_id"]:
                self._observe_migration(session, value, group)
            self._save(value)
            return self.public(value)

    def apply(self, session: Any, *, operation_id: str) -> dict:
        self._account(session)
        with self.lock:
            value = self._load(operation_id)
            if value["status"] != "prepared":
                return self.status(session, operation_id=operation_id) if value["status"] == "unknown" else self.public(value)
            if self._expire(value):
                return self.public(value)
            self._guard_unfinished(operation_id=operation_id, chat_id=value["chat_id"],
                basic_group_id=value["basic_group_id"])
            try:
                _, basic_id = self._chat(session, value["chat_id"])
                if basic_id != value["basic_group_id"]:
                    raise ValueError("Basic group identity changed since preparation")
                group = self._basic(session, basic_id)
                if self._observe_migration(session, value, group):
                    self._save(value)
                    return self.public(value)
                self._owner(group)
            except ValueError:
                value.update(status="failed", error="target_or_owner_changed")
                self._save(value)
                return self.public(value)
            except (TdlibError, TimeoutError, RuntimeError):
                value.update(error="preflight_unavailable")
                self._save(value)
                return self.public(value)
            if self._expire(value):
                return self.public(value)
            # Preflight can wait on network RPCs. Honor an owner opt-out that
            # happened while those reads were running, immediately before the
            # durable dispatch marker and the single native mutation.
            if self.can_dispatch() is not True:
                value.update(status="failed", error="group_upgrades_disabled")
                self._save(value)
                return self.public(value)
            value.update(status="unknown", dispatched=True, error="native_result_unknown")
            self._save(value)
            try:
                response = session.request({"@type": "upgradeBasicGroupChatToSupergroupChat", "chat_id": value["chat_id"]})
                new_chat_id, new_supergroup_id = self._new_chat(response,
                    basic_group_id=value["basic_group_id"], chat_id=value["chat_id"])
                value.update(new_chat_id=new_chat_id, new_supergroup_id=new_supergroup_id)
                self._save(value)
            except TdlibError as exc:
                if (exc.code <= 0 or exc.code == 408 or exc.code >= 500
                        or exc.code != 406 and exc.message.strip().lower() in {"request aborted", "request_aborted"}):
                    return self.public(value)
                value.update(status="failed", error="telegram_refused_group_upgrade")
                self._save(value)
                return self.public(value)
            except ValueError:
                value.update(error="invalid_native_response")
                self._save(value)
                return self.public(value)
            except (TimeoutError, RuntimeError):
                return self.public(value)
            return self.status(session, operation_id=operation_id)

    def status(self, session: Any, *, operation_id: str) -> dict:
        """Reconcile native migration links without ever dispatching an upgrade."""
        self._account(session)
        with self.lock:
            value = self._load(operation_id)
            if self._expire(value) or value["status"] == "failed":
                return self.public(value)
            try:
                _, basic_id = self._chat(session, value["chat_id"])
                if basic_id != value["basic_group_id"]:
                    raise ValueError("Basic group identity changed")
                group = self._basic(session, basic_id)
                if not self._observe_migration(session, value, group):
                    if value["dispatched"]:
                        value.update(status="unknown", error="upgrade_not_yet_observed")
                    else:
                        self._owner(group)
                        value.update(error=None, observed_at=time.time())
            except (ValueError, TdlibError, TimeoutError, RuntimeError):
                if value["dispatched"] or value["status"] == "already_upgraded":
                    value.update(status="unknown", error="status_readback_unavailable")
                else:
                    value.update(error="status_readback_unavailable")
            self._save(value)
            return self.public(value)
