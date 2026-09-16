"""Explicit additional operations shared by the MCP proxy and local service."""
# Keep this module stdlib-only: the installer imports service/wire before dependencies exist.
WORKFLOW_OPERATIONS = frozenset({"list_chats", "get_chat_history", "search_chat_messages",
    "get_message_thread", "get_scheduled_messages", "download_file", "get_chat_draft", "set_chat_draft"})


def contracts():
    from .navigation import REQUESTS, RESULTS
    from .downloads import DownloadRequest, DownloadResult
    from .chat_drafts import DraftRequest, SetDraftRequest, DraftResult
    return ({**REQUESTS, "download_file": DownloadRequest, "get_chat_draft": DraftRequest, "set_chat_draft": SetDraftRequest},
            {**RESULTS, "download_file": DownloadResult, "get_chat_draft": DraftResult, "set_chat_draft": DraftResult})


WRITE_OPERATIONS = frozenset({"set_chat_draft"})


class WorkflowMethods:
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
