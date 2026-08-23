#!/usr/bin/env python3
"""Dependency-free ShowMe command-line client."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlparse


CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_PATH = CONFIG_HOME / "showme" / "config.json"
UNINSTALLER = Path.home() / ".local" / "share" / "showme" / "uninstall.sh"


class ShowMeError(Exception):
    pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ShowMeError("ShowMe is not configured. Run: showme configure")
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ShowMeError(f"Cannot read {CONFIG_PATH}: {error}") from error
    if not config.get("server") or not config.get("token"):
        raise ShowMeError("The ShowMe config is incomplete. Run: showme configure")
    return config


def save_config(server: str, token: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"server": server.rstrip("/"), "token": token}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(CONFIG_PATH, 0o600)


def normalize_server_url(server: str) -> str:
    server = server.strip().rstrip("/")
    if server.startswith(("http://", "https://")):
        return server
    parsed = urlparse(f"//{server}")
    host = (parsed.hostname or "").lower()
    try:
        is_ip_address = bool(ipaddress.ip_address(host))
    except ValueError:
        is_ip_address = False
    scheme = "http" if host == "localhost" or is_ip_address else "https"
    return f"{scheme}://{server}"


def api_request(method: str, path: str, payload: dict | None = None, body: bytes | None = None, headers=None):
    config = load_config()
    request_headers = {"Authorization": f"Bearer {config['token']}", "Accept": "application/json"}
    request_headers.update(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{config['server']}{path}", data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", detail)
        except json.JSONDecodeError:
            message = detail or error.reason
        raise ShowMeError(f"Server returned {error.code}: {message}") from error
    except urllib.error.URLError as error:
        raise ShowMeError(f"Cannot reach the ShowMe server: {error.reason}") from error


def make_site_zip(site_dir: Path) -> Path:
    site_dir = site_dir.expanduser().resolve()
    if not site_dir.is_dir():
        raise ShowMeError(f"Not a directory: {site_dir}")
    if not (site_dir / "index.html").is_file():
        raise ShowMeError("The site directory must contain index.html at its root.")
    files = []
    for path in site_dir.rglob("*"):
        if path.is_symlink():
            raise ShowMeError(f"Symbolic links are not allowed: {path}")
        if path.is_file():
            files.append(path)
    handle, archive_name = tempfile.mkstemp(prefix="showme-", suffix=".zip")
    os.close(handle)
    archive_path = Path(archive_name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(site_dir).as_posix())
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def multipart_upload(
    archive_path: Path,
    fields: dict[str, str],
    method: str = "POST",
    path: str = "/api/pages",
) -> dict:
    boundary = f"----showme-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="site"; filename="site.zip"\r\n',
            b"Content-Type: application/zip\r\n\r\n",
            archive_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return api_request(
        method,
        path,
        body=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def print_page(result: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    print(f"Published: {result['url']}")
    print(f"ID:        {result['id']}")
    print(f"Visibility: {result['visibility']}")
    if result.get("passcode"):
        print(f"Passcode:  {result['passcode']}")
        print("Save this passcode; it will not be shown again.")


def command_configure(args) -> None:
    existing = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    default_server = existing.get("server") or "http://127.0.0.1"
    server = args.server or input(f"ShowMe server URL [{default_server}]: ").strip() or default_server
    token = args.token or getpass.getpass("API token: ").strip() or existing.get("token")
    if not server:
        raise ShowMeError("The server URL is required.")
    if not token:
        raise ShowMeError(
            "The API token is required. Paste a token from Dashboard → API tokens; the prompt stays visually blank while you type."
        )
    server = normalize_server_url(server)
    save_config(server, token)
    result = api_request("GET", "/api/pages")
    print(f"Configured {CONFIG_PATH}")
    print(f"Connected successfully ({len(result['pages'])} published pages).")


def command_publish(args) -> None:
    if args.passcode and not args.private:
        raise ShowMeError("--passcode requires --private.")
    archive = make_site_zip(Path(args.directory))
    fields = {
        "title": args.title or Path(args.directory).resolve().name,
        "visibility": "private" if args.private else "public",
    }
    if args.passcode:
        fields["passcode"] = args.passcode
    try:
        result = multipart_upload(archive, fields)
    finally:
        archive.unlink(missing_ok=True)
    print_page(result, args.json)


def page_id_from_reference(value: str) -> str:
    if value.startswith(("http://", "https://")):
        parts = [part for part in urlparse(value).path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "p":
            return parts[-1]
        raise ShowMeError("The page URL must have the form SERVER/p/PAGE_ID/.")
    return value


def command_update(args) -> None:
    page_id = page_id_from_reference(args.page)
    archive = make_site_zip(Path(args.directory))
    fields = {"title": args.title} if args.title else {}
    try:
        result = multipart_upload(
            archive,
            fields,
            method="PUT",
            path=f"/api/pages/{page_id}/content",
        )
    finally:
        archive.unlink(missing_ok=True)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"Updated: {result['url']}")
    print(f"ID:      {result['id']}")
    print(f"Title:   {result['title']}")


def command_list(args) -> None:
    result = api_request("GET", "/api/pages")
    if args.json:
        print(json.dumps(result, indent=2))
        return
    if not result["pages"]:
        print("No published pages.")
        return
    for page in result["pages"]:
        print(f"{page['id']:<14} {page['visibility']:<7} {page['status']:<8} {page['title']}")
        print(f"{'':<32}{page['url']}")


def command_visibility(args) -> None:
    if args.passcode and args.visibility != "private":
        raise ShowMeError("--passcode can only be used with private visibility.")
    payload = {"visibility": args.visibility}
    if args.passcode:
        payload["passcode"] = args.passcode
    result = api_request("PATCH", f"/api/pages/{args.page_id}", payload=payload)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"{result['id']} is now {result['visibility']}: {result['url']}")
    if result.get("passcode"):
        print(f"Passcode: {result['passcode']}")
        print("Save this passcode; it will not be shown again.")


def command_status(args) -> None:
    result = api_request("PATCH", f"/api/pages/{args.page_id}", payload={"status": args.status})
    print(f"{result['id']} is now {result['status']}.")


def command_delete(args) -> None:
    if not args.yes:
        answer = input(f"Permanently delete {args.page_id} and all of its assets? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return
    api_request("DELETE", f"/api/pages/{args.page_id}")
    print(f"Deleted {args.page_id}.")


def command_open(args) -> None:
    pages = api_request("GET", "/api/pages")["pages"]
    page = next((item for item in pages if item["id"] == args.page_id), None)
    if not page:
        raise ShowMeError("Page not found.")
    print(page["url"])
    webbrowser.open(page["url"])


def command_doctor(_args) -> None:
    config = load_config()
    result = api_request("GET", "/api/pages")
    print(f"Server: {config['server']}")
    print(f"Config: {CONFIG_PATH}")
    print(f"Status: connected ({len(result['pages'])} pages)")


def command_uninstall(args) -> None:
    if not UNINSTALLER.exists():
        raise ShowMeError(f"Uninstaller not found at {UNINSTALLER}. Re-run the repository installer first.")
    command = [str(UNINSTALLER)]
    if args.all:
        command.append("--all")
    elif args.keep_config:
        command.append("--keep-config")
    subprocess.run(command, check=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="showme", description="Publish webpage directories to a ShowMe server.")
    subcommands = root.add_subparsers(dest="command", required=True)

    configure = subcommands.add_parser("configure", help="Save the server URL and API token.")
    configure.add_argument("--server")
    configure.add_argument("--token", help="Avoid this flag on shared shells because it may enter shell history.")
    configure.set_defaults(func=command_configure)

    publish = subcommands.add_parser("publish", help="Publish a directory containing index.html.")
    publish.add_argument("directory")
    publish.add_argument("--title")
    publish.add_argument("--private", action="store_true")
    publish.add_argument("--passcode")
    publish.add_argument("--json", action="store_true")
    publish.set_defaults(func=command_publish)

    update = subcommands.add_parser("update", help="Replace a page's files without changing its URL.")
    update.add_argument("page", help="Page ID or full ShowMe page URL.")
    update.add_argument("directory")
    update.add_argument("--title", help="Optionally change the page title.")
    update.add_argument("--json", action="store_true")
    update.set_defaults(func=command_update)

    list_command = subcommands.add_parser("list", help="List published pages.")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(func=command_list)

    visibility = subcommands.add_parser("visibility", help="Switch a page between public and private.")
    visibility.add_argument("page_id")
    visibility.add_argument("visibility", choices=["public", "private"])
    visibility.add_argument("--passcode")
    visibility.add_argument("--json", action="store_true")
    visibility.set_defaults(func=command_visibility)

    archive = subcommands.add_parser("archive", help="Disable a page while retaining its files.")
    archive.add_argument("page_id")
    archive.set_defaults(func=command_status, status="archived")

    restore = subcommands.add_parser("restore", help="Restore an archived page.")
    restore.add_argument("page_id")
    restore.set_defaults(func=command_status, status="active")

    delete = subcommands.add_parser("delete", help="Permanently delete a page.")
    delete.add_argument("page_id")
    delete.add_argument("--yes", action="store_true")
    delete.set_defaults(func=command_delete)

    open_command = subcommands.add_parser("open", help="Open a published page in the browser.")
    open_command.add_argument("page_id")
    open_command.set_defaults(func=command_open)

    doctor = subcommands.add_parser("doctor", help="Check configuration and connectivity.")
    doctor.set_defaults(func=command_doctor)

    uninstall = subcommands.add_parser("uninstall", help="Remove the local CLI and Codex integration.")
    uninstall.add_argument("--all", action="store_true", help="Also remove saved configuration and token.")
    uninstall.add_argument("--keep-config", action="store_true")
    uninstall.set_defaults(func=command_uninstall)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except ShowMeError as error:
        print(f"showme: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
