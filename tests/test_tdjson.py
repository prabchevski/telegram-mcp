from __future__ import annotations

import os
import queue
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from telegram_search_mcp.tdjson import (
    AuthorizationController,
    AuthorizationMachine,
    GlobalSearchCursor,
    TdApi,
    TdClient,
    TdlibError,
    TdlibParameters,
    TdlibSchema,
    infer_schema_from_library_path,
    tdjson_library_candidates,
)


class FakeTransport:
    def __init__(self) -> None:
        self.client_id = 17
        self.sent: list[dict[str, Any]] = []
        self.incoming: queue.Queue[dict[str, Any]] = queue.Queue()

    def send(self, request: Mapping[str, Any]) -> None:
        self.sent.append(dict(request))

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def execute(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {"@type": "ok"}

    def respond(self, sent_index: int, **response: Any) -> None:
        self.incoming.put(
            {
                "@type": "ok",
                "@client_id": self.client_id,
                "@extra": self.sent[sent_index]["@extra"],
                **response,
            }
        )


def parameters() -> TdlibParameters:
    return TdlibParameters(
        api_id=12345,
        api_hash="secret-api-hash",
        database_directory="/tmp/td-test",
        database_encryption_key=b"database-key",
    )


class ParametersTests(unittest.TestCase):
    def test_current_parameters_are_flat_and_key_is_base64(self) -> None:
        request = parameters().initialization_request(TdlibSchema.CURRENT)
        self.assertEqual(request["@type"], "setTdlibParameters")
        self.assertNotIn("parameters", request)
        self.assertEqual(request["database_encryption_key"], "ZGF0YWJhc2Uta2V5")
        self.assertTrue(request["use_message_database"])

    def test_legacy_parameters_are_nested_and_key_is_separate(self) -> None:
        params = parameters()
        request = params.initialization_request(TdlibSchema.V1_8)
        self.assertEqual(request["@type"], "setTdlibParameters")
        self.assertEqual(request["parameters"]["api_id"], 12345)
        self.assertNotIn("database_encryption_key", request["parameters"])
        self.assertEqual(
            params.legacy_encryption_key_request()["encryption_key"],
            "ZGF0YWJhc2Uta2V5",
        )


class ApiTests(unittest.TestCase):
    def test_current_global_search_shape_and_cursor(self) -> None:
        api = TdApi("current")
        request = api.search_messages(
            "needle", cursor=GlobalSearchCursor(offset="opaque-next")
        )
        self.assertEqual(request["offset"], "opaque-next")
        self.assertIn("chat_type_filter", request)
        self.assertNotIn("offset_date", request)

        page = api.parse_search_messages(
            {
                "@type": "foundMessages",
                "total_count": 10,
                "messages": [{"id": 2, "chat_id": -10, "date": 100}],
                "next_offset": "page-2",
            }
        )
        self.assertEqual(page.next_cursor, GlobalSearchCursor(offset="page-2"))

    def test_legacy_global_search_shape_and_derived_cursor(self) -> None:
        api = TdApi("1.8")
        request = api.search_messages(
            "needle",
            cursor=GlobalSearchCursor(
                offset_date=123, offset_chat_id=-10, offset_message_id=99
            ),
        )
        self.assertEqual(request["offset_date"], 123)
        self.assertNotIn("offset", request)

        page = api.parse_search_messages(
            {
                "@type": "messages",
                "total_count": 1,
                "messages": [{"id": 99, "chat_id": -10, "date": 123}],
            }
        )
        self.assertEqual(
            page.next_cursor,
            GlobalSearchCursor(
                offset_date=123, offset_chat_id=-10, offset_message_id=99
            ),
        )

    def test_chat_search_shapes_differ(self) -> None:
        current = TdApi("current").search_chat_messages(-100, "needle")
        legacy = TdApi("1.8").search_chat_messages(-100, "needle")
        self.assertIn("topic_id", current)
        self.assertNotIn("message_thread_id", current)
        self.assertEqual(legacy["message_thread_id"], 0)
        self.assertNotIn("topic_id", legacy)

    def test_context_request_uses_negative_newer_offset(self) -> None:
        request = TdApi.get_message_context(-100, 500, older=4, newer=3)
        self.assertEqual(request["from_message_id"], 500)
        self.assertEqual(request["offset"], -3)
        self.assertEqual(request["limit"], 8)

    def test_context_is_capped_at_one_hundred(self) -> None:
        with self.assertRaises(ValueError):
            TdApi.get_message_context(-100, 500, older=60, newer=40)

    def test_bounded_synchronous_download_shape(self) -> None:
        request = TdApi.download_file(123, priority=32, limit=5_242_881)
        self.assertEqual(
            request,
            {
                "@type": "downloadFile",
                "file_id": 123,
                "priority": 32,
                "offset": 0,
                "limit": 5_242_881,
                "synchronous": True,
            },
        )

    def test_invalid_download_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TdApi.download_file(0)
        with self.assertRaises(ValueError):
            TdApi.download_file(1, priority=33)
        with self.assertRaises(ValueError):
            TdApi.download_file(1, limit=-1)


class CorrelationTests(unittest.TestCase):
    def test_out_of_order_responses_resolve_the_right_future(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport)
        first = client.send({"@type": "first"})
        second = client.send({"@type": "second"})

        transport.respond(1, value=2)
        transport.respond(0, value=1)
        client.process_one()
        client.process_one()

        self.assertEqual(first.result()["value"], 1)
        self.assertEqual(second.result()["value"], 2)

    def test_error_response_becomes_typed_exception(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport)
        future = client.send({"@type": "test"})
        transport.incoming.put(
            {
                "@type": "error",
                "code": 400,
                "message": "bad request",
                "@client_id": transport.client_id,
                "@extra": transport.sent[0]["@extra"],
            }
        )
        client.process_one()
        with self.assertRaises(TdlibError) as caught:
            future.result()
        self.assertEqual(caught.exception.code, 400)

    def test_updates_are_queued_and_dispatched(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport)
        seen: list[str] = []
        client.add_update_handler(lambda item: seen.append(item["@type"]))
        transport.incoming.put(
            {"@type": "updateConnectionState", "@client_id": 17}
        )
        client.process_one()
        self.assertEqual(seen, ["updateConnectionState"])
        self.assertEqual(client.updates.get_nowait()["@type"], "updateConnectionState")

    def test_timed_out_request_is_removed_and_late_response_is_ignored(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport)

        with self.assertRaises(TimeoutError):
            client.request({"@type": "slow"}, timeout=0.0)

        self.assertEqual(client._pending, {})
        transport.respond(0, value="late")
        self.assertTrue(client.process_one())
        self.assertEqual(client._abandoned, {})

    def test_timeout_waits_when_receiver_has_already_claimed_response(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport)
        future = client.send({"@type": "racing"})
        claimed = threading.Event()
        release = threading.Event()
        original_set_result = future.set_result

        def delayed_set_result(value: dict[str, Any]) -> None:
            claimed.set()
            self.assertTrue(release.wait(timeout=1.0))
            original_set_result(value)

        future.set_result = delayed_set_result  # type: ignore[method-assign]
        transport.respond(0, value="delivered")
        receiver = threading.Thread(target=client.process_one)
        receiver.start()
        self.assertTrue(claimed.wait(timeout=1.0))

        client.send = lambda _request: future  # type: ignore[method-assign]
        timer = threading.Timer(0.05, release.set)
        timer.start()
        try:
            result = client.request({"@type": "racing"}, timeout=0.0)
        finally:
            release.set()
            timer.cancel()
            receiver.join(timeout=1.0)

        self.assertEqual(result["value"], "delivered")
        self.assertFalse(future.cancelled())
        self.assertFalse(receiver.is_alive())


class AuthorizationTests(unittest.TestCase):
    def test_current_wait_parameters_produces_current_request(self) -> None:
        machine = AuthorizationMachine(parameters(), "current")
        requests = machine.consume(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {
                    "@type": "authorizationStateWaitTdlibParameters"
                },
            }
        )
        self.assertEqual(len(requests), 1)
        self.assertIn("database_encryption_key", requests[0])
        self.assertNotIn("parameters", requests[0])

    def test_legacy_encryption_state_produces_key_request(self) -> None:
        machine = AuthorizationMachine(parameters(), "1.8")
        requests = machine.consume(
            {"@type": "authorizationStateWaitEncryptionKey", "is_encrypted": True}
        )
        self.assertEqual(requests[0]["@type"], "checkDatabaseEncryptionKey")

    def test_email_code_uses_current_union_object(self) -> None:
        request = AuthorizationMachine(parameters(), "current").submit_email_code(
            "123456"
        )
        self.assertEqual(
            request["code"],
            {"@type": "emailAddressAuthenticationCode", "code": "123456"},
        )

    def test_bootstrap_response_alone_drives_the_machine(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport, receive_timeout=0.01)
        machine = AuthorizationMachine(parameters(), "1.8")
        controller = AuthorizationController(client, machine)

        bootstrap = controller.begin()
        transport.incoming.put(
            {
                "@type": "authorizationStateWaitTdlibParameters",
                "@client_id": transport.client_id,
                "@extra": transport.sent[0]["@extra"],
            }
        )
        self._wait_for_sent_type(transport, "setTdlibParameters")
        transport.respond(1)

        self.assertEqual(
            bootstrap.result(timeout=1)["@type"],
            "authorizationStateWaitTdlibParameters",
        )
        self.assertEqual(machine.state_type, "authorizationStateWaitTdlibParameters")
        client.stop()

    def test_bootstrap_update_and_response_do_not_duplicate_parameters(self) -> None:
        transport = FakeTransport()
        client = TdClient(transport, receive_timeout=0.01)
        machine = AuthorizationMachine(parameters(), "1.8")
        controller = AuthorizationController(client, machine)

        bootstrap = controller.begin()
        state = {"@type": "authorizationStateWaitTdlibParameters"}
        transport.incoming.put(
            {
                "@type": "updateAuthorizationState",
                "@client_id": transport.client_id,
                "authorization_state": state,
            }
        )
        transport.incoming.put(
            {
                **state,
                "@client_id": transport.client_id,
                "@extra": transport.sent[0]["@extra"],
            }
        )
        self._wait_for_sent_type(transport, "setTdlibParameters")
        transport.respond(1)
        bootstrap.result(timeout=1)
        time.sleep(0.02)

        sent_types = [item["@type"] for item in transport.sent]
        self.assertEqual(sent_types.count("setTdlibParameters"), 1)
        client.stop()

    @staticmethod
    def _wait_for_sent_type(
        transport: FakeTransport, object_type: str, timeout: float = 1.0
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(item.get("@type") == object_type for item in transport.sent):
                return
            time.sleep(0.005)
        raise AssertionError(f"request {object_type!r} wasn't sent")


class DiscoveryTests(unittest.TestCase):
    def test_ambient_library_override_is_ignored(self) -> None:
        injected = "/tmp/untrusted-workspace/libtdjson.dylib"
        explicit = "/tmp/explicit-test-only/libtdjson.dylib"
        with patch.dict(
            os.environ,
            {
                "TDJSON_LIBRARY": injected,
                "DYLD_LIBRARY_PATH": "/tmp/untrusted-workspace",
                "DYLD_FRAMEWORK_PATH": "/tmp/untrusted-frameworks",
            },
        ):
            candidates = tdjson_library_candidates(explicit)

        self.assertEqual(
            (candidates[0], *candidates[-2:]),
            (
                Path(explicit),
                Path("/opt/homebrew/opt/tdlib/lib/libtdjson.dylib"),
                Path("/usr/local/opt/tdlib/lib/libtdjson.dylib"),
            ),
        )

        self.assertNotIn(Path(injected), candidates)

    def test_homebrew_cellar_path_infers_legacy_schema(self) -> None:
        self.assertIs(
            infer_schema_from_library_path(
                "/opt/homebrew/Cellar/tdlib/1.8.0/lib/libtdjson.dylib"
            ),
            TdlibSchema.V1_8,
        )


if __name__ == "__main__":
    unittest.main()
