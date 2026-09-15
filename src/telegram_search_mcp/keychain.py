"""Small macOS Keychain adapter that keeps secrets out of argv and files."""

from __future__ import annotations

import getpass
import subprocess

from .profile_binding import credential_services

SERVICE = "local.unofficial-telegram-search-mcp-shared"
LEGACY_SERVICES: tuple[str, ...] = ()
SECURITY = "/usr/bin/security"


class KeychainError(RuntimeError):
    pass


def _account(profile: str, secret_name: str) -> str:
    return f"{profile}:{secret_name}"


def get_secret(secret_name: str, profile: str = "default") -> str | None:
    for service in credential_services():
        result = subprocess.run(
            [
                SECURITY,
                "find-generic-password",
                "-s",
                service,
                "-a",
                _account(profile, secret_name),
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 44:
            continue
        if result.returncode != 0:
            raise KeychainError(
                "Unable to read Telegram Search credential from Keychain"
            )
        return result.stdout.rstrip("\n")
    return None


def set_secret(secret_name: str, value: str, profile: str = "default") -> None:
    if not value:
        raise ValueError("Refusing to store an empty secret")
    # A bare final -w prompts twice. Detaching the child from the controlling
    # terminal makes `security` read both values from our private stdin pipe,
    # rather than opening /dev/tty. The secret never enters argv or a file.
    result = subprocess.run(
        [
            SECURITY,
            "add-generic-password",
            "-U",
            "-s",
            credential_services()[0],
            "-a",
            _account(profile, secret_name),
            "-l",
            f"Unofficial Telegram Search MCP ({profile}/{secret_name})",
            "-w",
        ],
        input=f"{value}\n{value}\n",
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    if result.returncode != 0:
        raise KeychainError("Unable to store Telegram Search credential in Keychain")


def delete_secret(secret_name: str, profile: str = "default") -> bool:
    deleted = False
    for service in credential_services():
        result = subprocess.run(
            [
                SECURITY,
                "delete-generic-password",
                "-s",
                service,
                "-a",
                _account(profile, secret_name),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 44:
            continue
        if result.returncode != 0:
            raise KeychainError(
                "Unable to delete Telegram Search credential from Keychain"
            )
        deleted = True
    return deleted


def prompt_and_store_api_hash(profile: str = "default") -> None:
    value = getpass.getpass("Telegram api_hash (stored only in macOS Keychain): ").strip()
    if len(value) < 16:
        raise ValueError("api_hash looks too short")
    set_secret("api_hash", value, profile)
