#!/usr/bin/env python3
"""Explicit optional smoke check: native TDLib start/close without auth or a database.

Run with the installed Python after Homebrew TDLib is present. No profile,
credentials, Telegram messages or client configuration are accessed.
"""
import threading

from telegram_search_mcp.tdjson import CtypesTdJsonTransport, TdClient


def main() -> None:
    for attempt in range(2):
        transport = CtypesTdJsonTransport(log_verbosity=0)
        client = TdClient(transport)
        closed = threading.Event()

        def on_update(update):
            if (update.get('@type') == 'updateAuthorizationState'
                    and update.get('authorization_state', {}).get('@type') == 'authorizationStateClosed'):
                closed.set()

        client.add_update_handler(on_update)
        client.start()
        try:
            state = client.request({'@type': 'getAuthorizationState'}, timeout=10)
            assert state.get('@type') == 'authorizationStateWaitTdlibParameters'
            client.request({'@type': 'close'}, timeout=10)
            assert closed.wait(10), 'Native close was not confirmed'
            print(f'PASS native TDLib lifecycle {attempt + 1}: initial state and confirmed close; no account/database supplied.')
        finally:
            client.stop()


if __name__ == '__main__':
    main()
