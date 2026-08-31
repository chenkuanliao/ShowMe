from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from pathlib import Path

import pytest


TEST_DATA = Path(os.environ["SHOWME_DATA_DIR"])

from showme_server.app import SETUP_TOKEN_PATH, create_app, with_page_title  # noqa: E402


@pytest.fixture(autouse=True)
def reset_server_data():
    shutil.rmtree(TEST_DATA, ignore_errors=True)
    TEST_DATA.mkdir(parents=True)
    yield


def site_zip(files: dict[str, bytes | str]) -> io.BytesIO:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    output.seek(0)
    return output


def setup_server(client, password: str = "x") -> None:
    token = SETUP_TOKEN_PATH.read_text(encoding="utf-8").strip()
    response = client.post(
        "/setup",
        data={"token": token, "password": password, "confirmation": password},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Your pages" in response.data


def create_api_token(client, name: str = "Test machine") -> str:
    page = client.get("/admin/tokens")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    assert match
    response = client.post(
        "/admin/tokens",
        data={"csrf_token": match.group(1).decode(), "name": name},
    )
    assert response.status_code == 200
    token_match = re.search(rb'<code id="generated-api-token">([^<]+)</code>', response.data)
    assert token_match
    assert b'data-copy-target="generated-api-token"' in response.data
    return token_match.group(1).decode()


def test_complete_page_lifecycle():
    app = create_app({"TESTING": True, "SECURE_COOKIES": False})
    client = app.test_client()
    setup_server(client)
    with app.app_context():
        from showme_server.app import get_db

        assert get_db().execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0
    api_token = create_api_token(client)
    headers = {"Authorization": f"Bearer {api_token}"}
    empty_dashboard = client.get("/admin")
    empty_version = re.search(rb'data-dashboard-version="([^"]+)"', empty_dashboard.data)
    assert empty_version

    response = client.post(
        "/api/pages",
        headers=headers,
        data={
            "title": "Test report",
            "visibility": "public",
            "site": (site_zip({"index.html": "<h1>Hello</h1><img src='./assets/dot.txt'>", "assets/dot.txt": "asset"}), "site.zip"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    page = response.get_json()
    page_id = page["id"]
    assert page["machine"] == "Test machine"
    assert page["description"] == ""
    assert page["url"] == f"http://showme.test/p/{page_id}/"
    served = client.get(f"/p/{page_id}/").data
    assert b"<title>Test report</title>" in served
    assert b"<h1>Hello</h1><img src='./assets/dot.txt'>" in served
    assert client.get(f"/p/{page_id}/assets/dot.txt").data == b"asset"
    published_dashboard = client.get("/admin")
    published_version = re.search(rb'data-dashboard-version="([^"]+)"', published_dashboard.data)
    assert published_version and published_version.group(1) != empty_version.group(1)
    assert f'data-page-id="{page_id}"'.encode() in published_dashboard.data
    assert b'data-page-search' in published_dashboard.data
    assert b'data-machine-filter' in published_dashboard.data
    assert re.search(rb'data-showme-asset="style" href="/static/style\.css\?v=[a-f0-9]{12}"', published_dashboard.data)
    assert re.search(rb'data-showme-asset="script" src="/static/app\.js\?v=[a-f0-9]{12}"', published_dashboard.data)
    assert b'<option value="Test machine">Test machine</option>' in published_dashboard.data
    assert b'data-machine="Test machine"' in published_dashboard.data

    csrf_match = re.search(rb'name="csrf_token" value="([^"]+)"', published_dashboard.data)
    assert csrf_match
    edit_page = client.get(f"/admin/pages/{page_id}/edit")
    assert edit_page.status_code == 200
    assert b"Edit page details" in edit_page.data
    response = client.post(
        f"/admin/pages/{page_id}/edit",
        data={
            "csrf_token": csrf_match.group(1).decode(),
            "title": "Benchmark & notes",
            "description": "GPU findings for the August comparison.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Benchmark &amp; notes" in response.data
    assert b"GPU findings for the August comparison." in response.data
    assert b"gpu findings for the august comparison." in response.data
    renamed_page = client.get(f"/p/{page_id}/").data
    assert b"<title>Benchmark &amp; notes</title>" in renamed_page
    assert b"<h1>Hello</h1>" in renamed_page
    api_page = client.get("/api/pages", headers=headers).get_json()["pages"][0]
    assert api_page["description"] == "GPU findings for the August comparison."

    download = client.get(f"/admin/pages/{page_id}/download")
    assert download.status_code == 200
    assert download.mimetype == "application/zip"
    assert f"Benchmark-notes-{page_id}.zip" in download.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
        root = f"Benchmark-notes-{page_id}"
        assert sorted(archive.namelist()) == [f"{root}/assets/dot.txt", f"{root}/index.html"]
        assert archive.read(f"{root}/index.html") == b"<h1>Hello</h1><img src='./assets/dot.txt'>"
        assert archive.read(f"{root}/assets/dot.txt") == b"asset"

    anonymous = app.test_client()
    assert anonymous.get(f"/admin/pages/{page_id}/download").status_code == 302
    assert anonymous.get(f"/api/pages/{page_id}/source").status_code == 401
    source = client.get(f"/api/pages/{page_id}/source", headers=headers)
    assert source.status_code == 200
    assert source.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(source.data)) as archive:
        assert sorted(archive.namelist()) == ["assets/dot.txt", "index.html"]
        assert archive.read("index.html") == b"<h1>Hello</h1><img src='./assets/dot.txt'>"
    assert client.get("/api/pages/does-not-exist/source", headers=headers).status_code == 404

    original_url = page["url"]
    response = client.put(
        f"/api/pages/{page_id}/content",
        headers=headers,
        data={
            "title": "Updated report",
            "site": (site_zip({"index.html": "<h1>Updated</h1>", "assets/new.txt": "new asset"}), "site.zip"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["url"] == original_url
    assert updated["title"] == "Updated report"
    assert b"<title>Updated report</title><h1>Updated</h1>" in client.get(f"/p/{page_id}/").data
    assert client.get(f"/p/{page_id}/assets/new.txt").data == b"new asset"
    assert client.get(f"/p/{page_id}/assets/dot.txt").status_code == 404

    response = client.put(
        f"/api/pages/{page_id}/content",
        headers=headers,
        data={"site": (site_zip({"notes.txt": "missing index"}), "site.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert b"<h1>Updated</h1>" in client.get(f"/p/{page_id}/").data

    dashboard = client.get("/admin")
    csrf_match = re.search(rb'name="csrf_token" value="([^"]+)"', dashboard.data)
    assert csrf_match
    response = client.post(
        f"/admin/pages/{page_id}/visibility",
        data={"csrf_token": csrf_match.group(1).decode(), "visibility": "private"},
    )
    assert response.status_code == 200
    passcode_match = re.search(rb'<code id="generated-passcode-[^"]+">([^<]+)</code>', response.data)
    assert passcode_match
    passcode = passcode_match.group(1).decode()
    assert len(passcode) == 14 and passcode.isalnum()
    assert f'data-copy-target="generated-passcode-{page_id}"'.encode() in response.data
    assert response.data.find(b"Private passcode") > response.data.find(page_id.encode())
    assert b"Enter its passcode" in client.get(f"/p/{page_id}/").data

    response = client.post(
        f"/p/{page_id}/unlock", data={"passcode": f"  {passcode}\n"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"<h1>Updated</h1>" in response.data

    response = client.patch(f"/api/pages/{page_id}", headers=headers, json={"visibility": "public"})
    assert response.get_json()["visibility"] == "public"

    response = client.patch(f"/api/pages/{page_id}", headers=headers, json={"status": "archived"})
    assert response.get_json()["status"] == "archived"
    assert client.get(f"/p/{page_id}/").status_code == 404

    response = client.patch(f"/api/pages/{page_id}", headers=headers, json={"status": "active"})
    assert response.get_json()["status"] == "active"
    assert client.get(f"/p/{page_id}/").status_code == 200

    assert client.delete(f"/api/pages/{page_id}", headers=headers).status_code == 204
    assert client.get(f"/p/{page_id}/").status_code == 404


def test_upload_validation_and_authentication():
    app = create_app({"TESTING": True, "SECURE_COOKIES": False})
    client = app.test_client()
    assert client.get("/api/pages").status_code == 401


def test_page_title_is_replaced_without_touching_body():
    document = "<!doctype html><html><head><title>Old title</title></head><body><h1>Old title stays here</h1></body></html>"
    result = with_page_title(document, "New <name>")
    assert "<title>New &lt;name&gt;</title>" in result
    assert "<h1>Old title stays here</h1>" in result
    assert result.count("<title>") == 1


def test_setup_and_dashboard_are_hardened():
    app = create_app({"TESTING": True, "SECURE_COOKIES": False})
    client = app.test_client()
    assert client.get("/setup?token=wrong").status_code in {302, 404}
    response = client.get("/admin")
    assert response.status_code == 302
    login = client.get("/admin/login")
    assert login.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert login.headers["Cache-Control"] == "no-store"


def test_rejects_unsafe_and_incomplete_archives():
    app = create_app({"TESTING": True, "SECURE_COOKIES": False})
    client = app.test_client()
    # Install a known test token directly; only its hash is persisted.
    with app.app_context():
        from showme_server.app import get_db, hash_secret, utcnow

        get_db().execute(
            "INSERT OR IGNORE INTO api_tokens(name, token_hash, created_at) VALUES(?, ?, ?)",
            ("validation", hash_secret("validation-token"), utcnow()),
        )
        get_db().commit()
    headers = {"Authorization": "Bearer validation-token"}

    missing_index = client.post(
        "/api/pages",
        headers=headers,
        data={"site": (site_zip({"report.html": "no index"}), "site.zip")},
        content_type="multipart/form-data",
    )
    assert missing_index.status_code == 400
    assert "index.html" in missing_index.get_json()["error"]

    unsafe = client.post(
        "/api/pages",
        headers=headers,
        data={"site": (site_zip({"index.html": "ok", "../escape.txt": "bad"}), "site.zip")},
        content_type="multipart/form-data",
    )
    assert unsafe.status_code == 400
    assert "unsafe path" in unsafe.get_json()["error"]
