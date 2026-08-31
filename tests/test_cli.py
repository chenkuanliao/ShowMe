from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest
from werkzeug.serving import make_server


SPEC = importlib.util.spec_from_file_location("showme_cli", Path(__file__).parents[1] / "cli" / "showme.py")
CLI = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(CLI)


def test_make_site_zip_preserves_relative_assets(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<img src='./assets/chart.png'>", encoding="utf-8")
    (tmp_path / "assets" / "chart.png").write_bytes(b"image")
    archive_path = CLI.make_site_zip(tmp_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert sorted(archive.namelist()) == ["assets/chart.png", "index.html"]
    finally:
        archive_path.unlink()


def test_make_site_zip_excludes_showme_metadata(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Report</h1>", encoding="utf-8")
    (tmp_path / ".showme.json").write_text('{"page_id":"abc"}', encoding="utf-8")
    (tmp_path / ".showme-workspace.json").write_text('{"id":"marker"}', encoding="utf-8")
    (tmp_path / ".showme-description.txt").write_text("draft", encoding="utf-8")
    archive_path = CLI.make_site_zip(tmp_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.namelist() == ["index.html"]
    finally:
        archive_path.unlink()


def test_make_site_zip_requires_root_index(tmp_path):
    (tmp_path / "report.html").write_text("hello", encoding="utf-8")
    with pytest.raises(CLI.ShowMeError, match="index.html"):
        CLI.make_site_zip(tmp_path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1"),
        ("192.168.1.40:8080", "http://192.168.1.40:8080"),
        ("203.0.113.10", "http://203.0.113.10"),
        ("[::1]:8080", "http://[::1]:8080"),
        ("localhost:8080/", "http://localhost:8080"),
        ("showme.example.com", "https://showme.example.com"),
        ("https://pages.example.com/", "https://pages.example.com"),
    ],
)
def test_normalize_server_url(value, expected):
    assert CLI.normalize_server_url(value) == expected


def test_page_id_from_reference():
    assert CLI.page_id_from_reference("abc123") == "abc123"
    assert CLI.page_id_from_reference("https://showme.example.com/p/abc123/") == "abc123"
    with pytest.raises(CLI.ShowMeError, match="PAGE_ID"):
        CLI.page_id_from_reference("https://showme.example.com/admin")


def test_description_arguments_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        CLI.parser().parse_args(
            [
                "publish",
                "report",
                "--description",
                "inline",
                "--description-file",
                "description.txt",
            ]
        )


def test_installer_publish_and_uninstaller_lifecycle(tmp_path):
    from showme_server.app import create_app, get_db, hash_secret, utcnow

    app = create_app({"TESTING": True, "SECURE_COOKIES": False})
    with app.app_context():
        get_db().execute(
            "INSERT OR IGNORE INTO api_tokens(name, token_hash, created_at) VALUES(?, ?, ?)",
            ("cli integration", hash_secret("cli-integration-token"), utcnow()),
        )
        get_db().commit()

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    repository = Path(__file__).parents[1]
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text(
        "Personal instructions\n\n<!-- showme:start -->\nold ShowMe instruction\n<!-- showme:end -->\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "CODEX_HOME": str(home / ".codex")})
    server_url = f"http://127.0.0.1:{server.server_port}"
    try:
        subprocess.run(
            [
                str(repository / "install.sh"),
                "--server",
                server_url,
                "--token",
                "cli-integration-token",
                "--codex-default",
            ],
            cwd=repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        installed_cli = home / ".local" / "bin" / "showme"
        assert installed_cli.is_file()
        assert (home / ".agents" / "skills" / "showme-publish" / "SKILL.md").is_file()
        agents_text = (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
        assert agents_text.count("<!-- showme:start -->") == 1
        assert "publish it automatically" in agents_text
        assert "old ShowMe instruction" not in agents_text
        assert "Personal instructions" in agents_text

        site = tmp_path / "site"
        site.mkdir()
        (site / "index.html").write_text("<h1>CLI integration</h1>", encoding="utf-8")
        published = subprocess.run(
            [
                str(installed_cli),
                "publish",
                str(site),
                "--title",
                "CLI integration",
                "--description",
                "Created to test searchable publishing context.",
                "--json",
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(published.stdout)
        assert result["title"] == "CLI integration"
        assert result["visibility"] == "public"
        assert result["machine"] == "cli integration"
        assert result["description"] == "Created to test searchable publishing context."

        retained = tmp_path / "retained-report"
        pulled_process = subprocess.run(
            [str(installed_cli), "pull", result["url"], str(retained), "--json"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        pulled = json.loads(pulled_process.stdout)
        assert pulled["id"] == result["id"]
        assert (retained / "index.html").read_text(encoding="utf-8") == "<h1>CLI integration</h1>"
        manifest = json.loads((retained / ".showme.json").read_text(encoding="utf-8"))
        assert manifest["page_id"] == result["id"]

        (retained / "index.html").write_text("<h1>Manifest update</h1>", encoding="utf-8")
        manifest_update = subprocess.run(
            [str(installed_cli), "update", str(retained), "--json"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        manifest_updated = json.loads(manifest_update.stdout)
        assert manifest_updated["id"] == result["id"]
        assert manifest_updated["description"] == "Created to test searchable publishing context."

        description_file = tmp_path / "description.txt"
        description_file.write_text(
            "Expanded to cover retained projects and remote workspaces.\n", encoding="utf-8"
        )
        described_update = subprocess.run(
            [
                str(installed_cli),
                "update",
                str(retained),
                "--description-file",
                str(description_file),
                "--json",
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(described_update.stdout)["description"] == (
            "Expanded to cover retained projects and remote workspaces."
        )

        details_process = subprocess.run(
            [
                str(installed_cli),
                "details",
                result["url"],
                "--title",
                "CLI details",
                "--description",
                "Metadata-only description update.",
                "--json",
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        details = json.loads(details_process.stdout)
        assert details["title"] == "CLI details"
        assert details["description"] == "Metadata-only description update."

        workspace_process = subprocess.run(
            [str(installed_cli), "workspace", "pull", result["id"], "--json"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        workspace = Path(json.loads(workspace_process.stdout)["directory"])
        assert workspace.is_dir()
        assert (workspace / ".showme-workspace.json").is_file()
        assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>Manifest update</h1>"
        subprocess.run(
            [str(installed_cli), "workspace", "clean", str(workspace)],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert not workspace.exists()
        refused_cleanup = subprocess.run(
            [str(installed_cli), "workspace", "clean", str(retained)],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert refused_cleanup.returncode == 1
        assert retained.is_dir()

        (site / "index.html").write_text("<h1>CLI updated</h1>", encoding="utf-8")
        updated_process = subprocess.run(
            [str(installed_cli), "update", result["url"], str(site), "--title", "CLI updated", "--json"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        updated = json.loads(updated_process.stdout)
        assert updated["id"] == result["id"]
        assert updated["url"] == result["url"]
        assert updated["title"] == "CLI updated"
        assert updated["description"] == "Metadata-only description update."

        upgraded = subprocess.run(
            [str(repository / "install.sh"), "--upgrade"],
            cwd=repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "ShowMe upgraded" in upgraded.stdout
        assert "Status: connected" in upgraded.stdout

        subprocess.run(
            [str(installed_cli), "uninstall", "--all"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert not installed_cli.exists()
        assert not (home / ".agents" / "skills" / "showme-publish").exists()
        assert "<!-- showme:start -->" not in (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
        assert not (home / ".config" / "showme").exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)
