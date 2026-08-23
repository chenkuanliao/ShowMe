from __future__ import annotations

import argparse
import os
import secrets

from .app import RECOVERY_TOKEN_PATH


def reset_password() -> None:
    token = secrets.token_urlsafe(32)
    RECOVERY_TOKEN_PATH.write_text(token, encoding="utf-8")
    os.chmod(RECOVERY_TOKEN_PATH, 0o600)
    base_url = os.environ.get("SHOWME_BASE_URL", "http://localhost:8080").rstrip("/")
    print("ShowMe administrator recovery link (one-time):")
    print(f"{base_url}/admin/recover?token={token}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="showme-admin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("reset-password", help="Generate a one-time administrator recovery link.")
    args = parser.parse_args()
    if args.command == "reset-password":
        reset_password()


if __name__ == "__main__":
    main()
