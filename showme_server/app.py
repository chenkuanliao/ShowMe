from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


DATA_DIR = Path(os.environ.get("SHOWME_DATA_DIR", "/data")).resolve()
DB_PATH = DATA_DIR / "showme.db"
SITES_DIR = DATA_DIR / "sites"
SECRET_PATH = DATA_DIR / "secret-key"
SETUP_TOKEN_PATH = DATA_DIR / "setup-token"
RECOVERY_TOKEN_PATH = DATA_DIR / "recovery-token"
MAX_UPLOAD_BYTES = int(os.environ.get("SHOWME_MAX_UPLOAD_MB", "50")) * 1024 * 1024
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        g.db = connection
    return g.db


def close_db(_error: object | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            csrf_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
            passcode_hash TEXT,
            status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
            source_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    page_columns = {row[1] for row in connection.execute("PRAGMA table_info(pages)")}
    if "source_name" not in page_columns:
        connection.execute("ALTER TABLE pages ADD COLUMN source_name TEXT")
        token_names = connection.execute("SELECT name FROM api_tokens").fetchall()
        if len(token_names) == 1:
            connection.execute(
                "UPDATE pages SET source_name = ? WHERE source_name IS NULL",
                (token_names[0][0],),
            )
    if "description" not in page_columns:
        connection.execute("ALTER TABLE pages ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    connection.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    admin_exists = connection.execute("SELECT 1 FROM admin WHERE id = 1").fetchone()
    connection.commit()
    connection.close()
    if not admin_exists:
        if not SETUP_TOKEN_PATH.exists():
            token = secrets.token_urlsafe(32)
            SETUP_TOKEN_PATH.write_text(token, encoding="utf-8")
            os.chmod(SETUP_TOKEN_PATH, 0o600)
        token = SETUP_TOKEN_PATH.read_text(encoding="utf-8").strip()
        base_url = os.environ.get("SHOWME_BASE_URL", "http://localhost:8080").rstrip("/")
        print("\nShowMe initial setup (one-time link):", flush=True)
        print(f"{base_url}/setup?token={token}\n", flush=True)


def load_or_create_secret() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_bytes(secrets.token_bytes(32))
        os.chmod(SECRET_PATH, 0o600)
    return SECRET_PATH.read_bytes()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    static_dir = Path(__file__).parent / "static"
    asset_bytes = b"".join(
        (static_dir / filename).read_bytes() for filename in ("style.css", "app.js")
    )
    app.jinja_env.globals["asset_version"] = hashlib.sha256(asset_bytes).hexdigest()[:12]
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=load_or_create_secret(),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 1024 * 1024,
        SECURE_COOKIES=os.environ.get("SHOWME_SECURE_COOKIES", "true").lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    init_db()
    app.teardown_appcontext(close_db)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.path.startswith("/admin") or request.path.startswith("/setup"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.get("/")
    def root():
        return redirect(url_for("dashboard"))

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if get_db().execute("SELECT 1 FROM admin WHERE id = 1").fetchone():
            return redirect(url_for("login"))
        supplied_token = request.values.get("token", "")
        expected_token = SETUP_TOKEN_PATH.read_text(encoding="utf-8").strip() if SETUP_TOKEN_PATH.exists() else ""
        if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
            abort(404)
        if request.method == "GET":
            return render_template("setup.html", token=supplied_token)
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        if not password:
            return render_template("setup.html", token=supplied_token, error="Enter a password."), 400
        if password != confirmation:
            return render_template("setup.html", token=supplied_token, error="The passwords do not match."), 400
        now = utcnow()
        db = get_db()
        try:
            db.execute(
                "INSERT INTO admin(id, password_hash, created_at, updated_at) VALUES(1, ?, ?, ?)",
                (PASSWORD_HASHER.hash(password), now, now),
            )
            db.commit()
        except sqlite3.IntegrityError:
            abort(409)
        SETUP_TOKEN_PATH.unlink(missing_ok=True)
        flash("Administrator created. Create an API token when you are ready to connect a machine.")
        response = redirect(url_for("dashboard"))
        set_admin_session(response)
        return response

    @app.route("/admin/login", methods=["GET", "POST"])
    def login():
        if current_session():
            return redirect(url_for("dashboard"))
        if request.method == "GET":
            return render_template("login.html")
        remote = request.remote_addr or "unknown"
        if login_blocked(remote):
            return render_template("login.html", error="Too many attempts. Try again later."), 429
        password = request.form.get("password", "")
        admin = get_db().execute("SELECT password_hash FROM admin WHERE id = 1").fetchone()
        valid = False
        if admin:
            try:
                valid = PASSWORD_HASHER.verify(admin["password_hash"], password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
        if not valid:
            record_login_failure(remote)
            return render_template("login.html", error="Incorrect username or password."), 401
        clear_login_failures(remote)
        response = redirect(url_for("dashboard"))
        set_admin_session(response)
        return response

    @app.route("/admin/recover", methods=["GET", "POST"])
    def recover():
        if not get_db().execute("SELECT 1 FROM admin WHERE id = 1").fetchone():
            return redirect(url_for("setup"))
        supplied_token = request.values.get("token", "")
        expected_token = RECOVERY_TOKEN_PATH.read_text(encoding="utf-8").strip() if RECOVERY_TOKEN_PATH.exists() else ""
        if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
            abort(404)
        if request.method == "GET":
            return render_template("recover.html", token=supplied_token)
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        if not password:
            return render_template("recover.html", token=supplied_token, error="Enter a password."), 400
        if password != confirmation:
            return render_template("recover.html", token=supplied_token, error="The passwords do not match."), 400
        get_db().execute(
            "UPDATE admin SET password_hash = ?, updated_at = ? WHERE id = 1",
            (PASSWORD_HASHER.hash(password), utcnow()),
        )
        get_db().execute("DELETE FROM sessions")
        get_db().commit()
        RECOVERY_TOKEN_PATH.unlink(missing_ok=True)
        flash("Password reset. Log in with the new password.")
        return redirect(url_for("login"))

    @app.post("/admin/logout")
    @admin_required
    def logout():
        require_csrf()
        token = request.cookies.get("showme_session", "")
        get_db().execute("DELETE FROM sessions WHERE token_hash = ?", (hash_secret(token),))
        get_db().commit()
        response = redirect(url_for("login"))
        response.delete_cookie("showme_session", path="/")
        return response

    @app.route("/admin", methods=["GET"])
    @admin_required
    def dashboard():
        return render_dashboard()

    @app.route("/admin/pages/<page_id>/edit", methods=["GET", "POST"])
    @admin_required
    def dashboard_edit(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            abort(404)
        if request.method == "GET":
            return render_template("edit_page.html", page=page)
        require_csrf()
        title = request.form.get("title", "").strip()[:160]
        description = request.form.get("description", "").strip()[:2000]
        if not title:
            return render_template(
                "edit_page.html",
                page=page,
                error="Give the page a name.",
                form_title=request.form.get("title", ""),
                form_description=description,
            ), 400
        get_db().execute(
            "UPDATE pages SET title = ?, description = ?, updated_at = ? WHERE id = ?",
            (title, description, utcnow(), page_id),
        )
        get_db().commit()
        flash("Page details updated.")
        return redirect(url_for("dashboard"))

    @app.get("/admin/pages/<page_id>/download")
    @admin_required
    def dashboard_download(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            abort(404)
        site_dir = SITES_DIR / page_id
        if not site_dir.is_dir():
            abort(404)
        safe_title = re.sub(r"[_-]+", "-", secure_filename(page["title"])).strip(".-") or "showme-page"
        folder_name = f"{safe_title}-{page_id}"
        archive_file = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(site_dir.rglob("*")):
                if source.is_file():
                    relative = source.relative_to(site_dir).as_posix()
                    archive.write(source, f"{folder_name}/{relative}")
        archive_file.seek(0)
        return send_file(
            archive_file,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{folder_name}.zip",
            max_age=0,
        )

    @app.get("/api/pages/<page_id>/source")
    @api_required
    def api_page_source(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            return jsonify(error="Page not found."), 404
        site_dir = SITES_DIR / page_id
        if not site_dir.is_dir():
            return jsonify(error="Page source is missing."), 404
        archive_file = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(site_dir.rglob("*")):
                if source.is_file():
                    archive.write(source, source.relative_to(site_dir).as_posix())
        archive_file.seek(0)
        response = send_file(
            archive_file,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"showme-{page_id}.zip",
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/admin/pages/<page_id>/visibility")
    @admin_required
    def dashboard_visibility(page_id: str):
        require_csrf()
        visibility = request.form.get("visibility", "")
        if visibility not in {"public", "private"}:
            abort(400)
        db = get_db()
        page = db.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            abort(404)
        generated_passcode = None
        passcode_hash = None
        if visibility == "private":
            generated_passcode = request.form.get("passcode", "").strip() or generate_passcode()
            passcode_hash = PASSWORD_HASHER.hash(generated_passcode)
        db.execute(
            "UPDATE pages SET visibility = ?, passcode_hash = ?, updated_at = ? WHERE id = ?",
            (visibility, passcode_hash, utcnow(), page_id),
        )
        db.commit()
        if generated_passcode:
            return render_dashboard(generated_passcode=generated_passcode, changed_page=page_id)
        flash("Visibility updated.")
        return redirect(url_for("dashboard"))

    @app.post("/admin/pages/<page_id>/status")
    @admin_required
    def dashboard_status(page_id: str):
        require_csrf()
        status_value = request.form.get("status", "")
        if status_value not in {"active", "archived"}:
            abort(400)
        cursor = get_db().execute(
            "UPDATE pages SET status = ?, updated_at = ? WHERE id = ?",
            (status_value, utcnow(), page_id),
        )
        if cursor.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Page status updated.")
        return redirect(url_for("dashboard"))

    @app.post("/admin/pages/<page_id>/delete")
    @admin_required
    def dashboard_delete(page_id: str):
        require_csrf()
        cursor = get_db().execute("DELETE FROM pages WHERE id = ?", (page_id,))
        if cursor.rowcount == 0:
            abort(404)
        get_db().commit()
        shutil.rmtree(SITES_DIR / page_id, ignore_errors=True)
        flash("Page permanently deleted.")
        return redirect(url_for("dashboard"))

    @app.get("/admin/pages/<page_id>/delete")
    @admin_required
    def dashboard_delete_confirm(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            abort(404)
        return render_template("delete_confirm.html", page=page)

    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def settings():
        if request.method == "POST":
            require_csrf()
            current = request.form.get("current_password", "")
            password = request.form.get("password", "")
            confirmation = request.form.get("confirmation", "")
            admin = get_db().execute("SELECT password_hash FROM admin WHERE id = 1").fetchone()
            try:
                current_valid = bool(admin) and PASSWORD_HASHER.verify(admin["password_hash"], current)
            except (VerifyMismatchError, InvalidHashError):
                current_valid = False
            if not current_valid:
                return render_template("settings.html", error="Current password is incorrect."), 400
            if not password:
                return render_template("settings.html", error="Enter a new password."), 400
            if password != confirmation:
                return render_template("settings.html", error="The passwords do not match."), 400
            get_db().execute(
                "UPDATE admin SET password_hash = ?, updated_at = ? WHERE id = 1",
                (PASSWORD_HASHER.hash(password), utcnow()),
            )
            get_db().execute("DELETE FROM sessions WHERE token_hash != ?", (hash_secret(request.cookies["showme_session"]),))
            get_db().commit()
            flash("Password changed. Other dashboard sessions were logged out.")
            return redirect(url_for("settings"))
        return render_template("settings.html")

    @app.route("/admin/tokens", methods=["GET", "POST"])
    @admin_required
    def tokens():
        generated_token = None
        if request.method == "POST":
            require_csrf()
            name = request.form.get("name", "").strip()[:80]
            if not name:
                return render_tokens(error="Give the token a name."), 400
            generated_token = secrets.token_urlsafe(32)
            get_db().execute(
                "INSERT INTO api_tokens(name, token_hash, created_at) VALUES(?, ?, ?)",
                (name, hash_secret(generated_token), utcnow()),
            )
            get_db().commit()
        return render_tokens(generated_token=generated_token)

    @app.post("/admin/tokens/<int:token_id>/revoke")
    @admin_required
    def revoke_token(token_id: int):
        require_csrf()
        get_db().execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
        get_db().commit()
        flash("API token revoked.")
        return redirect(url_for("tokens"))

    @app.post("/api/pages")
    @api_required
    def api_publish():
        upload = request.files.get("site")
        if not upload:
            return jsonify(error="A ZIP file is required in the 'site' field."), 400
        title = request.form.get("title", "Untitled page").strip()[:160] or "Untitled page"
        description = request.form.get("description", "").strip()[:2000]
        visibility = request.form.get("visibility", "public")
        if visibility not in {"public", "private"}:
            return jsonify(error="visibility must be public or private"), 400
        passcode = request.form.get("passcode", "").strip()
        generated_passcode = None
        if visibility == "private" and not passcode:
            passcode = generated_passcode = generate_passcode()
        page_id = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        try:
            extract_site(upload.read(), page_id)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        now = utcnow()
        try:
            get_db().execute(
                "INSERT INTO pages(id, title, description, visibility, passcode_hash, status, source_name, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (
                    page_id,
                    title,
                    description,
                    visibility,
                    PASSWORD_HASHER.hash(passcode) if passcode else None,
                    g.api_token["name"],
                    now,
                    now,
                ),
            )
            get_db().commit()
        except Exception:
            shutil.rmtree(SITES_DIR / page_id, ignore_errors=True)
            raise
        result = page_json(page_id)
        if generated_passcode:
            result["passcode"] = generated_passcode
        return jsonify(result), 201

    @app.get("/api/pages")
    @api_required
    def api_pages():
        rows = get_db().execute("SELECT * FROM pages ORDER BY created_at DESC").fetchall()
        return jsonify(pages=[page_json(row["id"], row) for row in rows])

    @app.put("/api/pages/<page_id>/content")
    @api_required
    def api_update_page_content(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            return jsonify(error="Page not found."), 404
        upload = request.files.get("site")
        if not upload:
            return jsonify(error="A ZIP file is required in the 'site' field."), 400
        try:
            extract_site(upload.read(), page_id, replace=True)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        title = request.form.get("title", "").strip()[:160] or page["title"]
        supplied_description = request.form.get("description")
        description = (
            supplied_description.strip()[:2000]
            if supplied_description is not None
            else page["description"]
        )
        get_db().execute(
            "UPDATE pages SET title = ?, description = ?, updated_at = ? WHERE id = ?",
            (title, description, utcnow(), page_id),
        )
        get_db().commit()
        return jsonify(page_json(page_id))

    @app.patch("/api/pages/<page_id>")
    @api_required
    def api_update_page(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            return jsonify(error="Page not found."), 404
        payload = request.get_json(silent=True) or {}
        visibility = payload.get("visibility", page["visibility"])
        status_value = payload.get("status", page["status"])
        title = str(payload.get("title", page["title"])).strip()[:160] or page["title"]
        description = str(payload.get("description", page["description"])).strip()[:2000]
        if visibility not in {"public", "private"} or status_value not in {"active", "archived"}:
            return jsonify(error="Invalid visibility or status."), 400
        generated_passcode = None
        passcode_hash = page["passcode_hash"]
        if visibility == "public":
            passcode_hash = None
        elif visibility != page["visibility"] or "passcode" in payload:
            passcode = str(payload.get("passcode", "")).strip()
            if not passcode:
                passcode = generated_passcode = generate_passcode()
            passcode_hash = PASSWORD_HASHER.hash(passcode)
        get_db().execute(
            "UPDATE pages SET title = ?, description = ?, visibility = ?, passcode_hash = ?, status = ?, updated_at = ? WHERE id = ?",
            (title, description, visibility, passcode_hash, status_value, utcnow(), page_id),
        )
        get_db().commit()
        result = page_json(page_id)
        if generated_passcode:
            result["passcode"] = generated_passcode
        return jsonify(result)

    @app.delete("/api/pages/<page_id>")
    @api_required
    def api_delete_page(page_id: str):
        cursor = get_db().execute("DELETE FROM pages WHERE id = ?", (page_id,))
        if cursor.rowcount == 0:
            return jsonify(error="Page not found."), 404
        get_db().commit()
        shutil.rmtree(SITES_DIR / page_id, ignore_errors=True)
        return "", 204

    @app.route("/p/<page_id>/unlock", methods=["POST"])
    def unlock_page(page_id: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ? AND status = 'active'", (page_id,)).fetchone()
        if not page or page["visibility"] != "private":
            abort(404)
        attempt_key = f"page:{page_id}:{request.remote_addr or 'unknown'}"
        if login_blocked(attempt_key):
            return render_template(
                "private_page.html", page=page, error="Too many attempts. Try again later."
            ), 429
        try:
            submitted_passcode = request.form.get("passcode", "").strip()
            valid = PASSWORD_HASHER.verify(page["passcode_hash"], submitted_passcode)
        except (VerifyMismatchError, InvalidHashError):
            valid = False
        if not valid:
            record_login_failure(attempt_key)
            return render_template("private_page.html", page=page, error="Incorrect passcode."), 401
        clear_login_failures(attempt_key)
        response = redirect(url_for("serve_page", page_id=page_id, asset_path=""))
        response.set_cookie(
            f"showme_access_{page_id}",
            page_access_value(page),
            max_age=30 * 24 * 60 * 60,
            secure=app.config["SECURE_COOKIES"],
            httponly=True,
            samesite="Strict",
            path=f"/p/{page_id}/",
        )
        return response

    @app.get("/p/<page_id>/", defaults={"asset_path": ""})
    @app.get("/p/<page_id>/<path:asset_path>")
    def serve_page(page_id: str, asset_path: str):
        page = get_db().execute("SELECT * FROM pages WHERE id = ? AND status = 'active'", (page_id,)).fetchone()
        if not page:
            abort(404)
        if page["visibility"] == "private" and not page_access_valid(page):
            return render_template("private_page.html", page=page)
        requested = asset_path or "index.html"
        if requested == "index.html":
            index_path = SITES_DIR / page_id / "index.html"
            if not index_path.is_file():
                abort(404)
            response = make_response(with_page_title(index_path.read_text(encoding="utf-8", errors="replace"), page["title"]))
            response.mimetype = "text/html"
            response.headers["Cache-Control"] = "no-cache"
        else:
            response = send_from_directory(SITES_DIR / page_id, requested)
        if page["visibility"] == "private":
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.errorhandler(413)
    def too_large(_error):
        if request.path.startswith("/api/"):
            return jsonify(error=f"Upload exceeds the {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit."), 413
        return "Upload too large", 413

    return app


def login_blocked(remote: str) -> bool:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    with _login_lock:
        attempts = [stamp for stamp in _login_attempts.get(remote, []) if stamp >= cutoff]
        _login_attempts[remote] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(remote: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(remote, []).append(time.time())


def clear_login_failures(remote: str) -> None:
    with _login_lock:
        _login_attempts.pop(remote, None)


def current_session():
    token = request.cookies.get("showme_session", "")
    if not token:
        return None
    return get_db().execute(
        "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
        (hash_secret(token), int(time.time())),
    ).fetchone()


def set_admin_session(response) -> None:
    token = secrets.token_urlsafe(32)
    get_db().execute(
        "INSERT INTO sessions(token_hash, csrf_token, expires_at, created_at) VALUES(?, ?, ?, ?)",
        (hash_secret(token), secrets.token_urlsafe(24), int(time.time()) + SESSION_SECONDS, utcnow()),
    )
    get_db().commit()
    response.set_cookie(
        "showme_session",
        token,
        max_age=SESSION_SECONDS,
        secure=current_app.config["SECURE_COOKIES"],
        httponly=True,
        samesite="Strict",
        path="/",
    )


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session = current_session()
        if not session:
            return redirect(url_for("login", next=request.path))
        g.admin_session = session
        return view(*args, **kwargs)

    return wrapped


def require_csrf() -> None:
    session = getattr(g, "admin_session", None) or current_session()
    supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not session or not supplied or not hmac.compare_digest(supplied, session["csrf_token"]):
        abort(403)


def api_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return jsonify(error="Missing API token."), 401
        token_hash = hash_secret(authorization[7:].strip())
        token = get_db().execute(
            "SELECT id, name FROM api_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not token:
            return jsonify(error="Invalid API token."), 401
        g.api_token = token
        get_db().execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (utcnow(), token["id"]))
        get_db().commit()
        return view(*args, **kwargs)

    return wrapped


def render_dashboard(**context):
    pages = get_db().execute("SELECT * FROM pages ORDER BY created_at DESC").fetchall()
    metrics = {
        "total": len(pages),
        "active": sum(page["status"] == "active" for page in pages),
        "private": sum(page["visibility"] == "private" for page in pages),
    }
    machines = sorted({page["source_name"] or "Unknown machine" for page in pages}, key=str.casefold)
    state = [
        [
            page["id"],
            page["title"],
            page["description"],
            page["visibility"],
            page["status"],
            page["source_name"],
            page["updated_at"],
        ]
        for page in pages
    ]
    dashboard_version = hash_secret(json.dumps(state, separators=(",", ":")))[:16]
    return render_template(
        "dashboard.html",
        pages=pages,
        metrics=metrics,
        machines=machines,
        dashboard_version=dashboard_version,
        **context,
    )


def render_tokens(**context):
    rows = get_db().execute(
        "SELECT id, name, created_at, last_used_at FROM api_tokens ORDER BY created_at DESC"
    ).fetchall()
    return render_template("tokens.html", tokens=rows, **context)


def generate_passcode() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(14))


def page_url(page_id: str) -> str:
    base = os.environ.get("SHOWME_BASE_URL", request.host_url).rstrip("/") + "/"
    return urljoin(base, f"p/{page_id}/")


def page_json(page_id: str, row=None) -> dict:
    row = row or get_db().execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "visibility": row["visibility"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "machine": row["source_name"] or "Unknown machine",
        "url": page_url(row["id"]),
    }


def with_page_title(document: str, title: str) -> str:
    """Return HTML with a display title without changing the uploaded file."""
    title_element = f"<title>{html.escape(title, quote=False)}</title>"
    existing_title = re.compile(r"<title\b[^>]*>.*?</title\s*>", re.IGNORECASE | re.DOTALL)
    if existing_title.search(document):
        return existing_title.sub(lambda _match: title_element, document, count=1)
    opening_head = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
    match = opening_head.search(document)
    if match:
        return document[: match.end()] + title_element + document[match.end() :]
    return title_element + document


def extract_site(archive_data: bytes, page_id: str, replace: bool = False) -> None:
    if not archive_data:
        raise ValueError("The uploaded ZIP is empty.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_data))
    except zipfile.BadZipFile as error:
        raise ValueError("The upload is not a valid ZIP file.") from error
    files = [entry for entry in archive.infolist() if not entry.is_dir()]
    if len(files) > 1000:
        raise ValueError("A site may contain at most 1000 files.")
    total_size = 0
    has_index = False
    for entry in files:
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("The ZIP contains an unsafe path.")
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("Symbolic links are not allowed.")
        total_size += entry.file_size
        if total_size > MAX_UPLOAD_BYTES:
            raise ValueError(f"Extracted content exceeds the {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit.")
        if entry.filename == "index.html":
            has_index = True
    if not has_index:
        raise ValueError("The site directory must contain index.html at its root.")
    temp_dir = Path(tempfile.mkdtemp(prefix="showme-", dir=SITES_DIR))
    destination = SITES_DIR / page_id
    backup = None
    try:
        for entry in files:
            target = temp_dir.joinpath(*PurePosixPath(entry.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        if destination.exists():
            if not replace:
                raise ValueError("A site with this ID already exists.")
            backup = SITES_DIR / f".backup-{page_id}-{secrets.token_hex(4)}"
            destination.replace(backup)
        temp_dir.replace(destination)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if backup and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        archive.close()
    if backup:
        shutil.rmtree(backup, ignore_errors=True)


def page_access_value(page) -> str:
    key = load_or_create_secret()
    message = f"{page['id']}:{page['passcode_hash']}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def page_access_valid(page) -> bool:
    supplied = request.cookies.get(f"showme_access_{page['id']}", "")
    return bool(supplied) and hmac.compare_digest(supplied, page_access_value(page))


app = create_app()
