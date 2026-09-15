"""Local terminal setup for Telegram credentials and TDLib authorization."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import secrets
import sys
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import qrcode

from .keychain import get_secret, prompt_and_store_api_hash, set_secret
from .policy import Policy, PolicyError, new_policy
from .tdjson import TdlibError, tdjson_library_candidates
from .tdlib_backend import TdlibSession, encode_database_key


def _configure_profile(profile: str, replace_api_hash: bool) -> Policy:
    try:
        policy = Policy.load(profile)
    except PolicyError as exc:
        if "Setup is incomplete" not in str(exc):
            raise
        raw = input("Telegram api_id from my.telegram.org: ").strip()
        try:
            policy = new_policy(int(raw))
        except (ValueError, PolicyError) as invalid:
            raise ValueError("api_id must be a positive integer") from invalid

    if replace_api_hash or get_secret("api_hash", profile) is None:
        prompt_and_store_api_hash(profile)
    if get_secret("database_key", profile) is None:
        set_secret(
            "database_key",
            encode_database_key(secrets.token_bytes(32)),
            profile,
        )
    policy.save(profile)
    return policy


def _render_qr(link: str) -> None:
    if not link.startswith("tg://login?"):
        raise RuntimeError("TDLib returned an unexpected QR authorization link")
    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.make(fit=True)
    print("\nScan this QR in Telegram: Settings → Devices → Link Desktop Device\n")
    qr.print_ascii(out=sys.stdout, tty=sys.stdout.isatty(), invert=True)
    print("\nWaiting for confirmation… (Ctrl-C cancels safely)\n")


def _await_submission(
    session: TdlibSession,
    previous: dict[str, Any],
    future: Future[dict[str, Any]],
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    future.result(timeout=30.0)
    return session.wait_for_state_change(previous, timeout=timeout)


def _authorization_loop(session: TdlibSession) -> int:
    assert session.authorization is not None
    state = session.wait_for_settled_state()
    while True:
        state_type = state.get("@type")
        if state_type == "authorizationStateReady":
            session.verify_runtime_version()
            me = session.request({"@type": "getMe"})
            user_id = int(me.get("id", 0))
            if user_id <= 0:
                raise RuntimeError("Telegram returned an invalid account identifier")
            return user_id

        if state_type == "authorizationStateWaitPhoneNumber":
            try:
                state = _await_submission(
                    session,
                    state,
                    session.authorization.send_qr_code_request(),
                )
            except TdlibError as exc:
                print(f"QR login unavailable ({exc.message}). Falling back to phone login.")
                phone = input("Telegram phone in international format (+…): ").strip()
                state = _await_submission(
                    session,
                    state,
                    session.authorization.send_phone_number(phone),
                )
            continue

        if state_type == "authorizationStateWaitOtherDeviceConfirmation":
            link = state.get("link")
            if not isinstance(link, str):
                raise RuntimeError("Telegram QR state contains no login link")
            _render_qr(link)
            state = session.wait_for_state_change(state, timeout=300.0)
            continue

        if state_type == "authorizationStateWaitCode":
            code = getpass.getpass("Telegram login code (input hidden): ").strip()
            try:
                state = _await_submission(
                    session,
                    state,
                    session.authorization.send_code(code),
                )
            except TdlibError as exc:
                print(f"Code rejected: {exc.message}")
                state = session.state or state
            continue

        if state_type == "authorizationStateWaitPassword":
            password = getpass.getpass("Telegram 2FA password (input hidden): ")
            try:
                state = _await_submission(
                    session,
                    state,
                    session.authorization.send_password(password),
                )
            except TdlibError as exc:
                print(f"Password rejected: {exc.message}")
                state = session.state or state
            continue

        if state_type == "authorizationStateWaitRegistration":
            raise RuntimeError(
                "This tool never creates Telegram accounts. Register in the official app first."
            )

        if state_type in {
            "authorizationStateLoggingOut",
            "authorizationStateClosing",
        }:
            state = session.wait_for_state_change(state, timeout=60.0)
            continue

        if state_type == "authorizationStateClosed":
            raise RuntimeError("Telegram closed the authorization session")

        raise RuntimeError(f"Unsupported Telegram authorization state: {state_type}")


def command_auth(args: argparse.Namespace) -> int:
    from .service import profile_exclusive

    # Exclude service startup before any credentials or account policy change.
    with profile_exclusive(args.profile):
        return _authorize_exclusive(args)


def _authorize_exclusive(args: argparse.Namespace) -> int:
    policy = _configure_profile(args.profile, args.replace_api_hash)
    session = TdlibSession(policy, args.profile)
    try:
        session.open()
        user_id = _authorization_loop(session)
        policy.bind_account(user_id)
        policy.save(args.profile)
        print(f"Authorized successfully. Telegram user ID: {user_id}")
        print("Global cloud-chat search is ready; secret chats remain excluded.")
        return 0
    finally:
        session.close()


def command_doctor(args: argparse.Namespace) -> int:
    policy = Policy.load(args.profile)
    if get_secret("api_hash", args.profile) is None:
        raise RuntimeError("api_hash is missing from macOS Keychain")
    if get_secret("database_key", args.profile) is None:
        raise RuntimeError("TDLib database key is missing from macOS Keychain")
    library = next((path for path in tdjson_library_candidates() if Path(path).exists()), None)
    if library is None:
        raise RuntimeError("libtdjson is not installed")
    print(f"Profile: {args.profile}")
    print(f"TDLib: {library}")
    print("Keychain credentials: present")
    print(f"Account binding: {policy.expected_user_id or 'not authorized'}")
    if policy.expected_user_id is None:
        raise RuntimeError("Telegram authorization is incomplete. Run `tgsearch auth` locally.")
    if not args.connect:
        return 0
    from .service_client import service_status

    result = asyncio.run(service_status(args.profile, connect=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_service(args: argparse.Namespace) -> int:
    from .service_client import ensure_service, service_status, stop_service

    if args.action == "start":
        asyncio.run(ensure_service(args.profile))
        result = asyncio.run(service_status(args.profile))
    elif args.action == "stop":
        result = asyncio.run(stop_service(args.profile, wait=True))
    else:
        result = asyncio.run(service_status(args.profile))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_update(args: argparse.Namespace) -> int:
    from .launchers import installed_root
    from .updater import update
    root = installed_root()
    if root is None:
        raise RuntimeError("Run updates from an installed copy")
    print(json.dumps(update(root, check_only=args.check), indent=2))
    return 0


def command_updates(args: argparse.Namespace) -> int:
    from .installation import read_receipt
    from .launchers import installed_root
    from .updater import set_enabled
    root = installed_root()
    if root is None:
        raise RuntimeError("Run updates from an installed copy")
    if args.action != "status":
        set_enabled(root, args.action == "on")
    receipt = read_receipt(root)
    print(json.dumps({"automatic": receipt.get("auto_update", False), "channel": "checked-main",
                      "interval_hours": 24, "revision": receipt.get("revision"), "version": receipt.get("version")}, indent=2))
    return 0


def command_sending(args: argparse.Namespace) -> int:
    from .launchers import installed_root
    from .sending_settings import set_sending, sending_enabled
    root = installed_root()
    if root is None:
        raise RuntimeError("Manage sending from an installed copy")
    if args.action != "status":
        set_sending(root, args.action == "on")
    print(json.dumps({"enabled": sending_enabled(root), "activation": "restart MCP clients; restart the idle service after upgrading"}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgsearch",
        description="Local Telegram search and optional sending setup",
    )
    from . import __version__

    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--profile", default="default", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    auth = commands.add_parser("auth", help="authorize TDLib by QR in this terminal")
    auth.add_argument(
        "--replace-api-hash",
        action="store_true",
        help="replace the api_hash stored in macOS Keychain",
    )
    auth.set_defaults(handler=command_auth)

    doctor = commands.add_parser("doctor", help="check local setup without exposing secrets")
    doctor.add_argument(
        "--connect",
        action="store_true",
        help="also open TDLib and verify the saved authorization",
    )
    doctor.set_defaults(handler=command_doctor)
    service = commands.add_parser("service", help="manage the shared local Telegram service")
    service.add_argument("action", choices=["start", "status", "stop"])
    service.set_defaults(handler=command_service)
    update = commands.add_parser("update", help="install the latest main revision that passed GitHub CI")
    update.add_argument("--check", action="store_true", help="check without installing")
    update.set_defaults(handler=command_update)
    updates = commands.add_parser("updates", help="manage daily automatic updates")
    updates.add_argument("action", choices=["status", "on", "off"])
    sending = commands.add_parser("sending", help="enable or disable text and file sending locally")
    sending.add_argument("action", choices=["status", "on", "off"])
    sending.set_defaults(handler=command_sending)
    updates.set_defaults(handler=command_updates)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        status = int(args.handler(args))
    except KeyboardInterrupt:
        print("\nCancelled; the Telegram session was closed without logging out.", file=sys.stderr)
        status = 130
    except Exception as exc:
        print(f"tgsearch: {exc}", file=sys.stderr)
        status = 1
    raise SystemExit(status)


if __name__ == "__main__":
    main()
