"""Explicit additional operations shared by the MCP proxy and local service."""
# Keep this module stdlib-only: the installer imports service/wire before dependencies exist.
WORKFLOW_OPERATIONS = frozenset({"list_chats", "get_chat_history", "search_chat_messages",
    "get_message_thread", "get_scheduled_messages", "download_file", "get_chat_draft", "set_chat_draft",
    "prepare_message_edit", "edit_message", "get_edit_status",
    "get_me", "get_pin_info", "pin_message", "unpin_message", "get_pin_status",
    "get_inline_bot_results", "prepare_forward", "prepare_inline_message",
    "prepare_group_upgrade", "upgrade_group", "get_group_upgrade_status"})


def contracts():
    from .navigation import REQUESTS, RESULTS
    from .downloads import DownloadRequest, DownloadResult
    from .chat_drafts import DraftRequest, SetDraftRequest, DraftResult
    from .editing import PrepareEditRequest, EditRequest, EditResult
    from .account_info import MeRequest, PinInfoRequest, MeResult, PinInfoResult
    from .pinning import PinRequest, UnpinRequest, PinStatusRequest, PinResult
    from .relaying import GetInlineResultsRequest, PrepareForwardRequest, PrepareInlineRequest, InlineResultsResult
    from .models import OutgoingResult
    from .group_upgrades import PrepareGroupUpgradeRequest, GroupUpgradeRequest, GroupUpgradeResult
    return ({**REQUESTS, "download_file": DownloadRequest, "get_chat_draft": DraftRequest, "set_chat_draft": SetDraftRequest,
             "prepare_message_edit": PrepareEditRequest, "edit_message": EditRequest, "get_edit_status": EditRequest,
             "get_me": MeRequest, "get_pin_info": PinInfoRequest, "pin_message": PinRequest,
             "unpin_message": UnpinRequest, "get_pin_status": PinStatusRequest,
             "get_inline_bot_results": GetInlineResultsRequest, "prepare_forward": PrepareForwardRequest,
             "prepare_inline_message": PrepareInlineRequest,
             "prepare_group_upgrade": PrepareGroupUpgradeRequest,
             "upgrade_group": GroupUpgradeRequest, "get_group_upgrade_status": GroupUpgradeRequest},
            {**RESULTS, "download_file": DownloadResult, "get_chat_draft": DraftResult, "set_chat_draft": DraftResult,
             "prepare_message_edit": EditResult, "edit_message": EditResult, "get_edit_status": EditResult,
             "get_me": MeResult, "get_pin_info": PinInfoResult, "pin_message": PinResult,
             "unpin_message": PinResult, "get_pin_status": PinResult,
             "get_inline_bot_results": InlineResultsResult, "prepare_forward": OutgoingResult,
             "prepare_inline_message": OutgoingResult,
             "prepare_group_upgrade": GroupUpgradeResult,
             "upgrade_group": GroupUpgradeResult, "get_group_upgrade_status": GroupUpgradeResult})


WRITE_OPERATIONS = frozenset({"set_chat_draft", "prepare_message_edit", "edit_message",
    "get_inline_bot_results", "prepare_forward", "prepare_inline_message"})
PIN_OPERATIONS = frozenset({"pin_message", "unpin_message"})
GROUP_UPGRADE_OPERATIONS = frozenset({"prepare_group_upgrade", "upgrade_group"})


class WorkflowMethods:
    async def prepare_group_upgrade(self, **params) -> dict:
        return await self._workflow("prepare_group_upgrade", params)

    async def upgrade_group(self, **params) -> dict:
        return await self._workflow("upgrade_group", params)

    async def get_group_upgrade_status(self, **params) -> dict:
        return await self._workflow("get_group_upgrade_status", params)

    async def get_inline_bot_results(self, **params) -> dict:
        return await self._workflow("get_inline_bot_results", params)

    async def prepare_forward(self, **params) -> dict:
        return await self._workflow("prepare_forward", params)

    async def prepare_inline_message(self, **params) -> dict:
        return await self._workflow("prepare_inline_message", params)

    async def get_me(self, **params) -> dict:
        return await self._workflow("get_me", params)

    async def get_pin_info(self, **params) -> dict:
        return await self._workflow("get_pin_info", params)

    async def pin_message(self, **params) -> dict:
        return await self._workflow("pin_message", params)

    async def unpin_message(self, **params) -> dict:
        return await self._workflow("unpin_message", params)

    async def get_pin_status(self, **params) -> dict:
        return await self._workflow("get_pin_status", params)

    async def prepare_message_edit(self, **params) -> dict:
        return await self._workflow("prepare_message_edit", params)

    async def edit_message(self, **params) -> dict:
        return await self._workflow("edit_message", params)

    async def get_edit_status(self, **params) -> dict:
        return await self._workflow("get_edit_status", params)

    async def get_chat_draft(self, **params) -> dict:
        return await self._workflow("get_chat_draft", params)

    async def set_chat_draft(self, **params) -> dict:
        return await self._workflow("set_chat_draft", params)

    async def download_file(self, **params) -> dict:
        return await self._workflow("download_file", params)

    async def list_chats(self, **params) -> dict:
        return await self._workflow("list_chats", params)

    async def get_chat_history(self, **params) -> dict:
        return await self._workflow("get_chat_history", params)

    async def search_chat_messages(self, **params) -> dict:
        return await self._workflow("search_chat_messages", params)

    async def get_message_thread(self, **params) -> dict:
        return await self._workflow("get_message_thread", params)

    async def get_scheduled_messages(self, **params) -> dict:
        return await self._workflow("get_scheduled_messages", params)


def execute(backend, session, operation, params):
    from .navigation import Navigation
    from .downloads import download
    from .chat_drafts import current, result, set_draft
    requests, _ = contracts()
    request = requests[operation].model_validate(params)
    if operation in GROUP_UPGRADE_OPERATIONS | {"get_group_upgrade_status"}:
        from .group_upgrades import GroupUpgrades
        from .group_upgrade_settings import group_upgrades_enabled
        from .paths import profile_root
        if not hasattr(backend, "_group_upgrades"):
            backend._group_upgrades = GroupUpgrades(profile_root(backend.profile) / "group-upgrades",
                                                   session.user_id, can_dispatch=group_upgrades_enabled)
        if backend._group_upgrades.user_id != session.user_id:
            raise ValueError("Group-upgrade account changed; restart the service")
        method = {"prepare_group_upgrade": "prepare", "upgrade_group": "apply",
                  "get_group_upgrade_status": "status"}[operation]
        return getattr(backend._group_upgrades, method)(session, **request.model_dump())
    if operation in {"get_inline_bot_results", "prepare_forward", "prepare_inline_message"}:
        from . import relaying
        from .outgoing import Outbox
        from .paths import profile_root
        directory = profile_root(backend.profile) / "inline-results"
        if operation == "get_inline_bot_results":
            return relaying.get_inline_results(session, directory=directory, **request.model_dump())
        base = profile_root(backend.profile)
        if (base / "outbox" / request.draft_id).exists():
            raise ValueError("draft_id already belongs to different content")
        if backend._relay_outbox is None:
            backend._relay_outbox = Outbox(base / "relay-outbox", session.user_id)
        if backend._relay_outbox.user_id != session.user_id:
            raise ValueError("Outgoing account changed; restart the service")
        backend._relay_outbox.attach(session.client)
        if operation == "prepare_forward":
            return relaying.prepare_forward(backend._relay_outbox, session, **request.model_dump())
        return relaying.prepare_inline(backend._relay_outbox, session, directory=directory, **request.model_dump())
    if operation == "get_me":
        from .account_info import get_me
        return get_me(session)
    if operation == "get_pin_info":
        from .account_info import get_pin_info
        return get_pin_info(session, request)
    if operation in PIN_OPERATIONS | {"get_pin_status"}:
        from .pinning import MessagePins
        from .paths import profile_root
        if not hasattr(backend, "_message_pins"):
            backend._message_pins = MessagePins(profile_root(backend.profile) / "message-pins", session.user_id)
        method = {"pin_message": "pin", "unpin_message": "unpin", "get_pin_status": "status"}[operation]
        return getattr(backend._message_pins, method)(session, **request.model_dump())
    if operation in {"prepare_message_edit", "edit_message", "get_edit_status"}:
        from .editing import MessageEdits
        from .paths import profile_root
        if not hasattr(backend, "_message_edits"):
            backend._message_edits = MessageEdits(profile_root(backend.profile) / "message-edits", session.user_id)
        if backend._message_edits.user_id != session.user_id:
            raise ValueError("Editing account changed; restart the service")
        method = {"prepare_message_edit": "prepare", "edit_message": "apply", "get_edit_status": "status"}[operation]
        return getattr(backend._message_edits, method)(session, **request.model_dump())
    if operation == "get_chat_draft":
        return result(request, current(session, request))
    if operation == "set_chat_draft":
        from .paths import profile_root
        return set_draft(session, request, profile_root(backend.profile) / "draft-operations")
    if operation == "download_file":
        from .paths import files_dir, profile_root
        return download(session, request, cache=files_dir(backend.profile), destination=profile_root(backend.profile) / "downloads")
    if not hasattr(backend, "_navigation"):
        backend._navigation = Navigation()
    if operation == "list_chats":
        return backend._navigation.list_chats(session, request)
    return backend._navigation.messages(session, request)


class BoundedSession:
    """One time budget across multi-request operations, inside the service deadline."""
    def __init__(self, session, seconds=75):
        import time
        self.session = session
        self.deadline = time.monotonic() + seconds

    def __getattr__(self, name):
        return getattr(self.session, name)

    def remaining(self, timeout):
        import time
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("Telegram operation reached its time budget")
        return min(left, timeout)

    def request(self, payload, timeout=15):
        return self.session.request(payload, timeout=self.remaining(timeout))

    def get_chat(self, chat_id, timeout=10):
        return self.session.get_chat(chat_id, timeout=self.remaining(timeout))
