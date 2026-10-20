#!/usr/bin/env python3
"""Serve the Steam report plus a small account API.

Binds to 127.0.0.1:10001 by default so an existing Cloudflare Tunnel keeps
working. Seen games and the last list position are stored per account in
SQLite and keyed by Steam appid, so they survive report regeneration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    "latest.html": "text/html; charset=utf-8",
    "latest.json": "application/json; charset=utf-8",
    "detail_cache.json": "application/json; charset=utf-8",
    "favicon.svg": "image/svg+xml",
    "report.js": "text/javascript; charset=utf-8",
}
USERNAME_RE = re.compile(r"^[\w.\-]{2,32}$")
LIST_KINDS = {"all", "game", "demo", "potential"}
SORTS = {"release-desc", "price-asc", "price-desc", "title-asc"}
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

_attempts_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}
_dummy_lock = threading.Lock()
_dummy_hash = ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS seen_apps (
    user_id INTEGER NOT NULL,
    appid TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (user_id, appid),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS positions (
    user_id INTEGER NOT NULL,
    list_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, list_key),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def db_path() -> Path:
    configured = os.environ.get("STEAM_REPORT_DB")
    if configured:
        return Path(configured)
    return ROOT / "data" / "accounts.sqlite"


def cookie_secure() -> bool:
    return os.environ.get("STEAM_REPORT_COOKIE_SECURE", "1") != "0"


def allow_register() -> bool:
    return os.environ.get("STEAM_REPORT_ALLOW_REGISTER", "1") != "0"


def session_days() -> int:
    try:
        return max(1, int(os.environ.get("STEAM_REPORT_SESSION_DAYS", "30")))
    except ValueError:
        return 30


def allowed_origins() -> set[str]:
    raw = os.environ.get("STEAM_REPORT_ALLOWED_ORIGINS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def dummy_password_hash() -> str:
    global _dummy_hash
    with _dummy_lock:
        if not _dummy_hash:
            _dummy_hash = hash_password("not-a-real-password")
        return _dummy_hash


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if n > 2**15 or r > 16 or p > 2 or n < 2:
            return False
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def validate_credentials(username: str, password: str) -> str | None:
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username.strip()):
        return "用户名需要 2 到 32 位，只能包含字母、数字、中文、下划线、点和连字符"
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return "密码至少 8 位，最多 128 位"
    return None


def register_user(conn: sqlite3.Connection, username: str, password: str) -> int:
    cleaned = username.strip()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, username_key, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (cleaned, cleaned.casefold(), hash_password(password), utcnow()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("用户名已存在") from exc
    return int(cursor.lastrowid)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username_key = ?",
        (username.strip().casefold(),),
    ).fetchone()
    stored = row["password_hash"] if row is not None else dummy_password_hash()
    if not verify_password(password, stored) or row is None:
        return None
    return {"id": int(row["id"]), "username": row["username"]}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    expires = (now + dt.timedelta(days=session_days())).isoformat()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash(token), user_id, now.isoformat(), expires),
    )
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
    return token


def session_cookie(token: str, *, clear: bool = False) -> str:
    secure = " Secure;" if cookie_secure() else ""
    max_age = 0 if clear else session_days() * 86400
    value = "" if clear else token
    return f"session={value}; HttpOnly;{secure} SameSite=Lax; Path=/; Max-Age={max_age}"


def client_key(handler: BaseHTTPRequestHandler) -> str:
    for header in ("CF-Connecting-IP", "X-Forwarded-For"):
        value = handler.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return handler.client_address[0]


def too_many_attempts(key: str) -> bool:
    now = time.time()
    with _attempts_lock:
        stamps = [stamp for stamp in _attempts.get(key, []) if now - stamp < 600]
        _attempts[key] = stamps
        return len(stamps) >= 8


def record_failure(key: str) -> None:
    now = time.time()
    with _attempts_lock:
        stamps = [stamp for stamp in _attempts.get(key, []) if now - stamp < 600]
        stamps.append(now)
        _attempts[key] = stamps


def clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def resolve_public(url_path: str) -> tuple[Path, str] | None:
    name = urllib.parse.unquote(url_path).split("?", 1)[0].strip("/")
    if name in {"", "index.html"}:
        index = ROOT / "index.html"
        candidate = index.resolve() if index.exists() or index.is_symlink() else (ROOT / "latest.html").resolve()
        content_type = "text/html; charset=utf-8"
    elif name in PUBLIC_FILES:
        candidate = (ROOT / name).resolve()
        content_type = PUBLIC_FILES[name]
    else:
        return None
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate, content_type


class Handler(BaseHTTPRequestHandler):
    server_version = "SteamReport/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_HEAD(self) -> None:
        self.route(head_only=True)

    def do_GET(self) -> None:
        self.route(head_only=False)

    def do_POST(self) -> None:
        self.route(head_only=False)

    def route(self, *, head_only: bool) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if self.command == "POST":
                self.handle_post(path)
                return
            if path == "/api/me" or path == "/api/auth/config":
                self.handle_me()
                return
            if path.startswith("/api/"):
                self.send_json(404, {"error": "没有这个接口"})
                return
            resolved = resolve_public(path)
            if resolved is None:
                self.send_text(404, "Not found\n")
                return
            file_path, content_type = resolved
            body = b"" if head_only or self.command == "HEAD" else file_path.read_bytes()
            length = file_path.stat().st_size if head_only or self.command == "HEAD" else len(body)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "服务器错误"})

    def handle_post(self, path: str) -> None:
        if not self.origin_allowed():
            self.send_json(403, {"error": "请求来源不被允许"})
            return
        try:
            payload = self.read_json()
        except ValueError:
            self.send_json(400, {"error": "请求格式不正确"})
            return
        if path == "/api/register":
            self.handle_register(payload)
        elif path == "/api/login":
            self.handle_login(payload)
        elif path == "/api/logout":
            self.handle_logout()
        elif path == "/api/seen":
            self.handle_seen(payload)
        elif path == "/api/position":
            self.handle_position(payload)
        else:
            self.send_json(404, {"error": "没有这个接口"})

    def handle_register(self, payload: dict[str, Any]) -> None:
        if not allow_register():
            self.send_json(403, {"error": "暂不开放注册"})
            return
        key = client_key(self)
        if too_many_attempts(key):
            self.send_json(429, {"error": "尝试过于频繁，请稍后再试"})
            return
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            self.send_json(400, {"error": "请填写用户名和密码"})
            return
        error = validate_credentials(username, password)
        if error:
            self.send_json(400, {"error": error})
            return
        try:
            with connect() as conn:
                user_id = register_user(conn, username, password)
                token = start_session(conn, user_id)
        except ValueError as exc:
            record_failure(key)
            self.send_json(409, {"error": str(exc)})
            return
        clear_failures(key)
        self.send_json(201, {"user": {"username": username.strip()}}, cookie=session_cookie(token))

    def handle_login(self, payload: dict[str, Any]) -> None:
        key = client_key(self)
        if too_many_attempts(key):
            self.send_json(429, {"error": "尝试过于频繁，请稍后再试"})
            return
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            self.send_json(400, {"error": "请填写用户名和密码"})
            return
        with connect() as conn:
            user = authenticate(conn, username, password)
            if user is None:
                record_failure(key)
                self.send_json(401, {"error": "用户名或密码不正确"})
                return
            token = start_session(conn, int(user["id"]))
            username_out = user["username"]
        clear_failures(key)
        self.send_json(200, {"user": {"username": username_out}}, cookie=session_cookie(token))

    def handle_logout(self) -> None:
        token = self.cookie_value("session")
        if token:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
        self.send_json(200, {"ok": True}, cookie=session_cookie("", clear=True))

    def handle_seen(self, payload: dict[str, Any]) -> None:
        user = self.require_user()
        if user is None:
            return
        raw_ids = payload.get("appids")
        if not isinstance(raw_ids, list) or len(raw_ids) > 200:
            self.send_json(400, {"error": "appids 需要是不超过 200 个的列表"})
            return
        appids: list[str] = []
        for value in raw_ids:
            text = str(value).strip()
            if text.isdigit() and len(text) <= 12 and text not in appids:
                appids.append(text)
        if not appids:
            self.send_json(400, {"error": "没有有效的 appid"})
            return
        seen = bool(payload.get("seen", True))
        now = utcnow()
        with connect() as conn:
            if seen:
                conn.executemany(
                    """
                    INSERT INTO seen_apps (user_id, appid, seen_at) VALUES (?, ?, ?)
                    ON CONFLICT(user_id, appid) DO UPDATE SET seen_at = excluded.seen_at
                    """,
                    [(int(user["id"]), appid, now) for appid in appids],
                )
            else:
                conn.executemany(
                    "DELETE FROM seen_apps WHERE user_id = ? AND appid = ?",
                    [(int(user["id"]), appid) for appid in appids],
                )
        self.send_json(200, {"ok": True, "appids": appids, "seen": seen})

    def handle_position(self, payload: dict[str, Any]) -> None:
        user = self.require_user()
        if user is None:
            return
        kind = payload.get("kind")
        if kind not in LIST_KINDS:
            self.send_json(400, {"error": "未知的列表"})
            return
        try:
            page = int(payload.get("page") or 1)
        except (TypeError, ValueError):
            self.send_json(400, {"error": "页码不正确"})
            return
        if page < 1 or page > 10000:
            self.send_json(400, {"error": "页码不正确"})
            return
        sort = payload.get("sort")
        if sort not in SORTS:
            sort = "release-desc"
        stored = {
            "kind": kind,
            "page": page,
            "paid_only": bool(payload.get("paid_only")),
            "hide_ai": bool(payload.get("hide_ai")),
            "unseen_only": bool(payload.get("unseen_only")),
            "sort": sort,
        }
        body = json.dumps(stored, ensure_ascii=False, separators=(",", ":"))
        now = utcnow()
        with connect() as conn:
            for list_key in (kind, "__last__"):
                conn.execute(
                    """
                    INSERT INTO positions (user_id, list_key, payload, updated_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, list_key) DO UPDATE SET
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    (int(user["id"]), list_key, body, now),
                )
        self.send_json(200, {"ok": True, "position": stored})

    def handle_me(self) -> None:
        with connect() as conn:
            user = self.user_from_session(conn)
            if user is None:
                self.send_json(200, {"user": None, "seen": [], "positions": {}, "allow_register": allow_register()})
                return
            token = self.cookie_value("session")
            if token:
                expires = (
                    dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(days=session_days())
                ).isoformat()
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                    (expires, token_hash(token)),
                )
            seen = [
                row["appid"]
                for row in conn.execute(
                    "SELECT appid FROM seen_apps WHERE user_id = ? ORDER BY appid",
                    (int(user["id"]),),
                ).fetchall()
            ]
            position_rows = [
                (row["list_key"], row["payload"])
                for row in conn.execute(
                    "SELECT list_key, payload FROM positions WHERE user_id = ?",
                    (int(user["id"]),),
                ).fetchall()
            ]
            username = user["username"]
        positions: dict[str, Any] = {}
        for list_key, payload in position_rows:
            try:
                positions[list_key] = json.loads(payload)
            except json.JSONDecodeError:
                continue
        self.send_json(
            200,
            {
                "user": {"username": username},
                "seen": seen,
                "positions": positions,
                "allow_register": allow_register(),
            },
        )

    def require_user(self) -> dict[str, Any] | None:
        with connect() as conn:
            user = self.user_from_session(conn)
        if user is None:
            self.send_json(401, {"error": "请先登录"})
            return None
        return user

    def user_from_session(self, conn: sqlite3.Connection) -> dict[str, Any] | None:
        token = self.cookie_value("session")
        if not token:
            return None
        row = conn.execute(
            """
            SELECT users.id, users.username
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash(token), utcnow()),
        ).fetchone()
        if row is None:
            return None
        return {"id": int(row["id"]), "username": row["username"]}

    def cookie_value(self, name: str) -> str | None:
        header = self.headers.get("Cookie") or ""
        for part in header.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name and value:
                return value
        return None

    def origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin in allowed_origins():
            return True
        host = self.headers.get("Host", "")
        return urllib.parse.urlparse(origin).netloc == host

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise ValueError("length") from exc
        if length < 0 or length > 65536:
            raise ValueError("length")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("json") from exc
        if not isinstance(payload, dict):
            raise ValueError("object")
        return payload

    def send_json(self, status: int, payload: dict[str, Any], *, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Steam report and account API.")
    parser.add_argument("--host", default=os.environ.get("STEAM_REPORT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STEAM_REPORT_PORT", "10001")))
    parser.add_argument("command", nargs="?", choices=["create-user"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--password", default="")
    args = parser.parse_args(argv)
    init_db()
    if args.command == "create-user":
        if not args.username:
            raise SystemExit("Usage: python3 server.py create-user USERNAME")
        password = args.password or os.environ.get("STEAM_REPORT_PASSWORD", "")
        if not password:
            if not sys.stdin.isatty():
                raise SystemExit("Set --password or STEAM_REPORT_PASSWORD")
            password = getpass.getpass("Password: ")
            again = getpass.getpass("Repeat password: ")
            if password != again:
                raise SystemExit("Passwords do not match")
        error = validate_credentials(args.username, password)
        if error:
            raise SystemExit(error)
        try:
            with connect() as conn:
                register_user(conn, args.username, password)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Created user {args.username.strip()}")
        return 0

    httpd = ThreadingServer((args.host, args.port), Handler)
    print(f"Serving Steam report on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
