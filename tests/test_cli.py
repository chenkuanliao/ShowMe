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


@pytest.mark.parametrize("agent", ["codex", "claude", "both"])
def test_installer_publish_and_uninstaller_lifecycle(tmp_path, agent):
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
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text(
        "Claude personal instructions\n\n<!-- showme:start -->\nold Claude instruction\n<!-- showme:end -->\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "CODEX_HOME": str(home / ".codex"), "XDG_CONFIG_HOME": str(home / ".config")})
    server_url = f"http://127.0.0.1:{server.server_port}"
    try:
        subprocess.run(
            [
                str(repository / "install.sh"),
                "--server",
                server_url,
                "--token",
                "cli-integration-token",
                "--agent",
                "codex" if agent == "both" else agent,
                *(["--codex-default"] if agent in ("codex", "both") else []),
                *(["--claude-default"] if agent == "claude" else []),
            ],
            cwd=repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        if agent == "both":
            subprocess.run(
                [str(repository / "install.sh"), "--agent", "both", "--codex-default", "--claude-default",
                 "--server", server_url, "--token", "cli-integration-token"],
                env=environment, check=True, capture_output=True, text=True,
            )
        installed_cli = home / ".local" / "bin" / "showme"
        assert installed_cli.is_file()
        integrations = []
        if agent in ("codex", "both"):
            integrations.append((home / ".agents" / "skills" / "showme-publish", home / ".codex" / "AGENTS.md", "Personal instructions"))
        if agent in ("claude", "both"):
            integrations.append((home / ".claude" / "skills" / "showme-publish", home / ".claude" / "CLAUDE.md", "Claude personal instructions"))
        for skill, instructions, personal in integrations:
            assert (skill / "SKILL.md").is_file()
            instructions_text = instructions.read_text(encoding="utf-8")
            assert instructions_text.count("<!-- showme:start -->") == 1
            assert "publish it automatically" in instructions_text
            assert "old " not in instructions_text
            assert personal in instructions_text
        if agent == "claude":
            assert not (home / ".agents" / "skills" / "showme-publish").exists()
        if agent == "codex":
            # Older installations stored only four manifest lines.
            manifest = home / ".local" / "share" / "showme" / "install-manifest"
            manifest.write_text("\n".join(manifest.read_text().splitlines()[:4]) + "\n")

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

        if agent == "both":
            codex_instructions = home / ".codex" / "AGENTS.md"
            codex_before = codex_instructions.read_bytes()
            for preference in ("--no-claude-default", "--claude-default"):
                subprocess.run(
                    [str(repository / "install.sh"), "--agent", "claude", preference,
                     "--server", server_url, "--token", "cli-integration-token"],
                    env=environment, check=True, capture_output=True, text=True,
                )
                assert codex_instructions.read_bytes() == codex_before
                claude_text = (home / ".claude" / "CLAUDE.md").read_text()
                assert ("<!-- showme:start -->" in claude_text) == (preference == "--claude-default")
                assert "Claude personal instructions" in claude_text
                assert (home / ".agents" / "skills" / "showme-publish" / "SKILL.md").is_file()
            # Use the recorded Codex path even when CODEX_HOME changes later.
            environment["CODEX_HOME"] = str(home / "different-codex-home")

        saved_config = (home / ".config" / "showme" / "config.json").read_bytes()
        preferences = {path: path.read_bytes() for _, path, _ in integrations}
        for skill, _, _ in integrations:
            (skill / "SKILL.md").write_text("stale skill")
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

        assert (home / ".config" / "showme" / "config.json").read_bytes() == saved_config
        for skill, instructions, _ in integrations:
            assert "Publish with ShowMe" in (skill / "SKILL.md").read_text()
            assert instructions.read_bytes() == preferences[instructions]

        subprocess.run(
            [str(installed_cli), "uninstall", "--all"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert not installed_cli.exists()
        assert not (home / ".agents" / "skills" / "showme-publish").exists()
        for skill, instructions, personal in integrations:
            assert not skill.exists()
            assert "<!-- showme:start -->" not in instructions.read_text()
            assert personal in instructions.read_text()
        if agent == "claude":
            assert "old ShowMe instruction" in (home / ".codex" / "AGENTS.md").read_text()
        if agent == "codex":
            assert "old Claude instruction" in (home / ".claude" / "CLAUDE.md").read_text()
        assert not (home / ".config" / "showme").exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize("arguments", [
    ["--agent", "invalid"],
    ["--agent", "claude", "--codex-default"],
    ["--agent", "codex", "--claude-default"],
    ["--upgrade", "--agent", "both"],
])
def test_installer_rejects_invalid_options_without_changes(tmp_path, arguments):
    repository = Path(__file__).parents[1]
    environment = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run([str(repository / "install.sh"), *arguments], env=environment, capture_output=True, text=True)
    assert result.returncode == 2
    assert not list(tmp_path.iterdir())


def test_installer_preserves_unmanaged_claude_skill(tmp_path):
    repository = Path(__file__).parents[1]
    skill = tmp_path / ".claude" / "skills" / "showme-publish"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Personal skill")
    environment = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [str(repository / "install.sh"), "--agent", "both", "--no-codex-default", "--no-claude-default"],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "unmanaged skill" in result.stderr
    assert (skill / "SKILL.md").read_text() == "Personal skill"
    assert not (tmp_path / ".local" / "bin" / "showme").exists()
    assert not (tmp_path / ".agents").exists()
