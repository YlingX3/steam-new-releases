import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STEAM_REPORT_DB"] = str(Path(self.tmp.name) / "accounts.sqlite")
        os.environ["STEAM_REPORT_COOKIE_SECURE"] = "0"
        os.environ["STEAM_REPORT_ALLOW_REGISTER"] = "1"
        os.environ.pop("STEAM_REPORT_ALLOWED_ORIGINS", None)
        server.clear_failures("127.0.0.1")
        server.init_db()
        self.httpd = server.ThreadingServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, jar: CookieJar | None = None, headers: dict | None = None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        cookie_jar = jar if jar is not None else CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        try:
            body = response.read().decode()
            header_map = {key: value for key, value in response.headers.items()}
        finally:
            response.close()
        parsed = json.loads(body) if body.startswith("{") else {"raw": body}
        return response.status, parsed, header_map

    def open_raw(self, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_home_serves_report_and_hides_private_files(self) -> None:
        status, body = self.open_raw("/")
        self.assertEqual(status, 200)
        self.assertIn("隐藏 AI 参与作品", body.decode())
        status, _body = self.open_raw("/.git/config")
        self.assertEqual(status, 404)
        status, _body = self.open_raw("/server.py")
        self.assertEqual(status, 404)
        status, body = self.open_raw("/report.js")
        self.assertEqual(status, 200)
        self.assertIn("selectBulkTargets", body.decode())

    def test_register_login_seen_position_and_isolation(self) -> None:
        jar = CookieJar()
        status, payload, headers = self.request(
            "POST", "/api/register", {"username": "yling", "password": "correct-horse"}, jar
        )
        self.assertEqual(status, 201, payload)
        cookie = headers.get("Set-Cookie") or ""
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn("password", json.dumps(payload))

        status, me, _headers = self.request("GET", "/api/me", jar=jar)
        self.assertEqual(me["user"]["username"], "yling")
        self.assertEqual(me["seen"], [])

        status, payload, _headers = self.request(
            "POST", "/api/seen", {"appids": ["2847640", "10", "nope"], "seen": True}, jar
        )
        self.assertEqual(status, 200, payload)
        status, me, _headers = self.request("GET", "/api/me", jar=jar)
        self.assertEqual(me["seen"], ["10", "2847640"])

        status, payload, _headers = self.request(
            "POST", "/api/seen", {"appids": ["10"], "seen": False}, jar
        )
        self.assertEqual(status, 200)
        status, me, _headers = self.request("GET", "/api/me", jar=jar)
        self.assertEqual(me["seen"], ["2847640"])

        status, payload, _headers = self.request(
            "POST",
            "/api/position",
            {"kind": "game", "page": 3, "hide_ai": True, "unseen_only": True, "paid_only": False, "sort": "price-asc"},
            jar,
        )
        self.assertEqual(status, 200, payload)
        status, me, _headers = self.request("GET", "/api/me", jar=jar)
        self.assertEqual(me["positions"]["__last__"]["page"], 3)
        self.assertEqual(me["positions"]["game"]["kind"], "game")
        self.assertTrue(me["positions"]["__last__"]["hide_ai"])
        self.assertTrue(me["positions"]["__last__"]["unseen_only"])

        other = CookieJar()
        status, payload, _headers = self.request(
            "POST", "/api/register", {"username": "other", "password": "another-pass"}, other
        )
        self.assertEqual(status, 201, payload)
        status, me, _headers = self.request("GET", "/api/me", jar=other)
        self.assertEqual(me["seen"], [])
        self.assertEqual(me["positions"], {})

        status, payload, _headers = self.request("POST", "/api/logout", {}, jar)
        self.assertEqual(status, 200)
        status, me, _headers = self.request("GET", "/api/me", jar=jar)
        self.assertIsNone(me["user"])

        status, payload, _headers = self.request(
            "POST", "/api/login", {"username": "yling", "password": "wrong-password"}, CookieJar()
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "用户名或密码不正确")

        restored = CookieJar()
        status, payload, _headers = self.request(
            "POST", "/api/login", {"username": "Yling", "password": "correct-horse"}, restored
        )
        self.assertEqual(status, 200, payload)
        status, me, _headers = self.request("GET", "/api/me", jar=restored)
        self.assertEqual(me["seen"], ["2847640"])

    def test_seen_requires_login_and_bad_origin_is_rejected(self) -> None:
        status, payload, _headers = self.request("POST", "/api/seen", {"appids": ["1"], "seen": True})
        self.assertEqual(status, 401)
        status, payload, _headers = self.request(
            "POST",
            "/api/login",
            {"username": "yling", "password": "correct-horse"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_secure_cookie_and_closed_registration(self) -> None:
        os.environ["STEAM_REPORT_COOKIE_SECURE"] = "1"
        os.environ["STEAM_REPORT_ALLOW_REGISTER"] = "0"
        status, payload, headers = self.request(
            "POST", "/api/register", {"username": "closed", "password": "correct-horse"}
        )
        self.assertEqual(status, 403, payload)
        with server.connect() as conn:
            server.register_user(conn, "cli-user", "correct-horse")
        status, payload, headers = self.request(
            "POST", "/api/login", {"username": "cli-user", "password": "correct-horse"}
        )
        self.assertEqual(status, 200, payload)
        self.assertIn("Secure", headers.get("Set-Cookie") or "")

    def test_password_is_stored_hashed(self) -> None:
        with server.connect() as conn:
            server.register_user(conn, "hashed", "correct-horse")
            row = conn.execute("SELECT password_hash FROM users WHERE username_key = ?", ("hashed",)).fetchone()
        self.assertTrue(row["password_hash"].startswith("scrypt$"))
        self.assertNotIn("correct-horse", row["password_hash"])
        self.assertTrue(server.verify_password("correct-horse", row["password_hash"]))
        self.assertFalse(server.verify_password("wrong-password", row["password_hash"]))


if __name__ == "__main__":
    unittest.main()
