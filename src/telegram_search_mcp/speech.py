"""Telegram-native speech recognition; no media download or external speech service."""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import time

from .backend import MediaError
from .paths import ensure_private_dir
from .policy import _assert_private_file, _atomic_private_json
from .tdjson import TdlibError

MAX_TRANSCRIPT_CHARS = 32000


def voice_payload(message: dict) -> dict | None:
    content = message.get("content") or {}
    key = {"messageVoiceNote": "voice_note", "messageVideoNote": "video_note"}.get(content.get("@type"))
    return content.get(key) if key else None


def check_message(message: dict, chat_id: int, message_id: int) -> None:
    if message.get("chat_id") != chat_id or message.get("id") != message_id:
        raise MediaError("Telegram returned a different message")
    if (message.get("can_be_saved") is False or message.get("self_destruct_type")
            or message.get("self_destruct_in", 0) or message.get("ttl", 0)
            or message.get("ttl_expires_in", 0) or (message.get("content") or {}).get("is_secret")):
        raise MediaError("Protected or self-destructing Telegram media is not available")
    if not isinstance(voice_payload(message), dict):
        raise MediaError("The selected message is not a voice note or video note")


def transcribe(session, directory: Path, *, chat_id: int, message_id: int,
               wait_seconds: int = 20, start: bool = True) -> dict:
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 60 or type(start) is not bool:
        raise ValueError("wait_seconds must be 0..60; start must be a boolean")
    operation_deadline = time.monotonic() + 75
    ensure_private_dir(directory)
    marker = directory / f"{chat_id}_{message_id}.json"
    if marker.exists() or marker.is_symlink():
        _assert_private_file(marker)
        if marker.stat().st_size > 1024 or json.loads(marker.read_text()).get("user_id") != session.user_id:
            raise MediaError("Speech request belongs to a different account or is invalid")

    def fetch():
        message = session.request({"@type": "getMessage", "chat_id": chat_id, "message_id": message_id}, timeout=10.0)
        check_message(message, chat_id, message_id)
        return voice_payload(message).get("speech_recognition_result")

    def output(status, text="", error_code=None, retry_after=None):
        return {"chat_id": chat_id, "message_id": message_id, "status": status,
                "text": text[:MAX_TRANSCRIPT_CHARS], "truncated": len(text) > MAX_TRANSCRIPT_CHARS,
                "error_code": error_code, "retry_after_seconds": retry_after}

    def failure(error):
        description = str(error.get("message", ""))
        if "PREMIUM" in description.upper():
            return output("unavailable", error_code="premium_required")
        flood = re.search(r"(?:FLOOD_WAIT_|retry after )(\d+)", description, re.I)
        if error.get("code") == 429 or flood:
            return output("unavailable", error_code="quota_or_rate_limit", retry_after=int(flood[1]) if flood else None)
        if "TOO_LONG" in description.upper():
            return output("unavailable", error_code="voice_too_long")
        return output("failed", error_code="telegram_transcription_failed")

    result = fetch()
    if result is None and not marker.exists() and start:
        properties = session.request({"@type": "getMessageProperties", "chat_id": chat_id, "message_id": message_id}, timeout=10.0)
        if properties.get("can_recognize_speech") is not True:
            return output("unavailable", error_code="not_available_for_account_or_message")
        # Persist before dispatch. A timeout, client cancellation or restart must
        # never trigger a second quota-consuming request for the same message.
        _atomic_private_json(marker, {"user_id": session.user_id, "requested": True})
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            session.request({"@type": "recognizeSpeech", "chat_id": chat_id, "message_id": message_id}, timeout=10.0)
        except TdlibError as exc:
            marker.unlink()  # Explicit rejection; no accepted transcription to poll.
            return failure(exc.response)
        except TimeoutError:
            return output("pending", error_code="request_outcome_unknown")
        result = fetch()
    elif result is None and not marker.exists():
        return output("not_started")

    deadline = min(time.monotonic() + wait_seconds, operation_deadline - 10)
    while True:
        kind = result.get("@type") if isinstance(result, dict) else None
        if kind == "speechRecognitionResultText":
            return output("completed", result.get("text", ""))
        if kind == "speechRecognitionResultError":
            return failure(result.get("error") or {})
        if time.monotonic() >= deadline:
            return output("pending", (result or {}).get("partial_text", ""))
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        result = fetch()
