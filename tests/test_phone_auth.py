"""Phone-number registration and login: validation, normalization, seed accounts and migration."""

import contextlib
import io
import json
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import database
import server

SEED_PHONES = {"lin": "13900000001", "chen": "13900000002", "wang": "13900000003",
               "zhao": "13900000004", "zhou": "13900000005", "admin": "13900000000"}
PHONE_MESSAGE = "请输入 11 位手机号，例如 13812345678。"
LOGIN_FAILED = "手机号或密码不正确"


class PhoneAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for patcher in (patch.object(database, "DB_FILE", root / "test.sqlite3"),
                        patch.object(database, "CREDENTIAL_FILE", root / ".credentials.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def stop_server(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(); server.SESSIONS.clear()

    def request(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def register(self, phone, password="abc12345", name="新客户"):
        return self.request("/api/auth/register", {"name": name, "phone": phone, "password": password})

    def seed_passwords(self):
        return json.loads(database.CREDENTIAL_FILE.read_text())["users"]

    def test_invalid_phone_is_rejected_on_register_login_and_switch(self):
        invalid = ["1381234567", "138123456789", "12812345678", "1381234567a", "", "abc", "138 1234 567", "２３８12345678"]
        for phone in invalid:
            with self.assertRaises(ValueError, msg=phone):
                server.normalize_phone(phone)
            status, payload = self.register(phone)
            self.assertEqual((status, payload.get("field"), payload.get("error")), (400, "phone", PHONE_MESSAGE), phone)
            status, payload = self.request("/api/auth/login", {"phone": phone, "password": "abc12345"})
            self.assertEqual((status, payload.get("field")), (400, "phone"), phone)
        for raw in (None, 13812345678, ["13812345678"]):
            with self.assertRaises(ValueError):
                server.normalize_phone(raw)
        token = self.request("/api/auth/login", {"phone": SEED_PHONES["lin"], "password": self.seed_passwords()["lin"]})[1]["token"]
        status, payload = self.request("/api/auth/switch", {"phone": "123", "password": "abc12345"}, token)
        self.assertEqual((status, payload.get("field")), (400, "phone"))
        self.assertIn(token, server.SESSIONS)

    def test_spaces_and_hyphens_are_normalized(self):
        for raw in ("138 1234 5678", "138-1234-5678", " 138 - 1234 - 5678 ", "13812345678"):
            self.assertEqual(server.normalize_phone(raw), "13812345678")
        status, payload = self.register("138 1234 5678")
        self.assertEqual(status, 201)
        self.assertEqual(database.get_user(payload["user"]["id"])["phone"], "13812345678")
        self.assertEqual(database.get_user(payload["user"]["id"])["username"], "13812345678")
        for raw in ("138-1234-5678", "13812345678", "138 1234 5678"):
            self.assertEqual(self.request("/api/auth/login", {"phone": raw, "password": "abc12345"})[0], 200, raw)

    def test_password_length_boundaries_at_registration(self):
        for length, phone in ((7, "13800000021"), (33, "13800000022")):
            status, payload = self.register(phone, "x" * length)
            self.assertEqual((status, payload.get("field")), (400, "password"), length)
        self.assertEqual(self.register("13800000021", "x" * 7)[1]["error"], "密码至少要 8 位，例如 abc12345。")
        self.assertEqual(self.register("13800000022", "x" * 33)[1]["error"], "密码最多 32 位，请缩短一些。")
        self.assertEqual(self.register("13800000023", "")[1]["field"], "password")
        self.assertEqual(self.register("13800000024", "x" * 8)[0], 201)
        self.assertEqual(self.register("13800000025", "x" * 32)[0], 201)
        # Length counts characters, not bytes.
        self.assertEqual(self.register("13800000026", "密" * 32)[0], 201)
        self.assertEqual(self.register("13800000027", "密" * 33)[0], 400)

    def test_password_has_no_character_composition_rule(self):
        for index, password in enumerate(("12345678", "abcdefgh", "!!!!!!!!", "        ")):
            phone = f"1380000003{index}"
            self.assertEqual(self.register(phone, password)[0], 201, password)
            self.assertEqual(self.request("/api/auth/login", {"phone": phone, "password": password})[0], 200)
        for password in ("12345678", "x" * 32, "x" * 8):
            self.assertEqual(server.validate_new_password(password), password)
        self.assertEqual((server.PASSWORD_MIN, server.PASSWORD_MAX), (8, 32))

    def test_name_is_validated_with_field(self):
        for name in ("", "   ", "名" * 21, None):
            status, payload = self.request("/api/auth/register", {"name": name, "phone": "13800000040", "password": "abc12345"})
            self.assertEqual((status, payload.get("field")), (400, "name"), name)

    def test_duplicate_registration_conflicts_on_phone(self):
        self.assertEqual(self.register("13800000050")[0], 201)
        for phone in ("13800000050", "138-0000-0050", SEED_PHONES["lin"]):
            status, payload = self.register(phone)
            self.assertEqual(status, 409, phone)
            self.assertEqual(payload, {"error": "这个手机号已经注册过，请直接登录。", "field": "phone"})
        with self.assertRaises(ValueError) as caught:
            database.create_customer_account("重复", "13800000050", "abc12345", 2, 365, 0)
        self.assertEqual(str(caught.exception), "phone_exists")

    def test_registration_ignores_username_field_and_logs_in_by_phone(self):
        status, payload = self.request("/api/auth/register", {"name": "新客户", "username": "chosen_name", "phone": "13800000060", "password": "abc12345"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["user"]["username"], "13800000060")
        self.assertNotIn("phone", payload["user"])
        self.assertEqual(self.request("/api/auth/me", token=payload["token"])[0], 200)
        status, payload = self.request("/api/auth/login", {"username": "chosen_name", "password": "abc12345"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["field"], "phone")

    def test_seed_accounts_log_in_by_phone_and_old_username_is_rejected(self):
        passwords = self.seed_passwords()
        self.assertEqual(set(passwords), set(SEED_PHONES))
        for username, phone in SEED_PHONES.items():
            status, payload = self.request("/api/auth/login", {"phone": phone, "password": passwords[username]})
            self.assertEqual(status, 200, username)
            self.assertEqual(payload["user"]["username"], username)
            status, payload = self.request("/api/auth/login", {"username": username, "password": passwords[username]})
            self.assertEqual(status, 400, username)
            self.assertNotIn("token", payload)
            # A seed account name is not a phone number, so it never reaches the credential lookup either.
            self.assertIsNone(database.get_user_by_phone_credentials(username, passwords[username]))
        self.assertEqual({user["username"]: user["phone"] for user in database.get_users_public()}, SEED_PHONES)

    def test_login_failure_message_does_not_reveal_registration(self):
        self.register("13800000070")
        wrong_password = self.request("/api/auth/login", {"phone": "13800000070", "password": "wrong-password"})
        unknown_phone = self.request("/api/auth/login", {"phone": "13800000071", "password": "abc12345"})
        self.assertEqual(wrong_password, (401, {"error": LOGIN_FAILED}))
        self.assertEqual(unknown_phone, (401, {"error": LOGIN_FAILED}))
        token = self.request("/api/auth/login", {"phone": "13800000070", "password": "abc12345"})[1]["token"]
        self.assertEqual(self.request("/api/auth/switch", {"phone": "13800000071", "password": "abc12345"}, token), (401, {"error": LOGIN_FAILED}))
        self.assertIn(token, server.SESSIONS)

    def test_login_and_switch_only_require_a_non_empty_password(self):
        old_password = "abc12"
        database.create_customer_account("老密码", "13800000080", old_password, 2, 365, 0)
        status, payload = self.request("/api/auth/login", {"phone": "13800000080", "password": old_password})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/auth/switch", {"phone": SEED_PHONES["lin"], "password": self.seed_passwords()["lin"]}, payload["token"])[0], 200)
        self.assertEqual(self.request("/api/auth/login", {"phone": "13800000080", "password": "x" * 100})[0], 401)
        for password in ("", None):
            status, payload = self.request("/api/auth/login", {"phone": "13800000080", "password": password})
            self.assertEqual((status, payload.get("field")), (400, "password"))

    def test_switch_with_phone_replaces_the_session(self):
        passwords = self.seed_passwords()
        token = self.request("/api/auth/login", {"phone": SEED_PHONES["lin"], "password": passwords["lin"]})[1]["token"]
        status, switched = self.request("/api/auth/switch", {"phone": "139-0000-0002", "password": passwords["chen"]}, token)
        self.assertEqual((status, switched["user"]["username"]), (200, "chen"))
        self.assertEqual(self.request("/api/auth/me", token=token)[0], 401)
        self.assertEqual(self.request("/api/auth/me", token=switched["token"])[0], 200)
        self.assertEqual(self.request("/api/auth/switch", {"phone": SEED_PHONES["lin"], "password": passwords["lin"]})[0], 401)

    def drop_phone_column(self):
        with database.connect() as connection:
            connection.execute("DROP INDEX users_phone")
            connection.execute("ALTER TABLE users DROP COLUMN phone")

    def test_database_without_phone_column_is_migrated_idempotently(self):
        passwords = self.seed_passwords()
        self.drop_phone_column()
        with database.connect() as connection:
            self.assertNotIn("phone", {row[1] for row in connection.execute("PRAGMA table_info(users)")})
        with contextlib.redirect_stdout(io.StringIO()):
            database.init_db()
        with database.connect() as connection:
            after_first = [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
            self.assertIn("users_phone", {row[1] for row in connection.execute("PRAGMA index_list(users)")})
        database.init_db()
        with database.connect() as connection:
            self.assertEqual(after_first, [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")])
        for username, phone in SEED_PHONES.items():
            self.assertEqual(database.get_user_by_phone_credentials(phone, passwords[username])["username"], username)
        self.assertEqual(self.seed_passwords(), passwords)

    def test_migration_keeps_existing_phones_and_skips_taken_demo_numbers(self):
        passwords = self.seed_passwords()
        self.register("13800000090")
        with database.connect() as connection:
            connection.execute("UPDATE users SET phone = NULL WHERE username IN ('lin', 'chen')")
            connection.execute("UPDATE users SET phone = '13900000009' WHERE username = 'zhou'")
        database.create_customer_account("占用者", SEED_PHONES["lin"], "abc12345", 2, 365, 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            database.init_db()
        users = {user["username"]: user["phone"] for user in database.get_users_public()}
        self.assertIsNone(users["lin"])
        self.assertIn(SEED_PHONES["lin"], output.getvalue())
        self.assertEqual(users["chen"], SEED_PHONES["chen"])
        self.assertEqual(users["zhou"], "13900000009")
        self.assertEqual(users["13800000090"], "13800000090")
        self.assertIsNone(database.get_user_by_phone_credentials(SEED_PHONES["lin"], passwords["lin"]))
        self.assertIsNotNone(database.get_user_by_phone_credentials(SEED_PHONES["chen"], passwords["chen"]))

    def test_phone_unique_index_rejects_duplicates_but_allows_many_null_phones(self):
        with database.connect() as connection:
            connection.execute("UPDATE users SET phone = NULL")
            connection.execute("UPDATE users SET phone = '13900000001' WHERE username = 'lin'")
            with self.assertRaises(database.sqlite3.IntegrityError):
                connection.execute("UPDATE users SET phone = '13900000001' WHERE username = 'chen'")

    def test_random_password_with_new_rule_registers_and_logs_in(self):
        password = secrets.token_urlsafe(12)
        self.assertEqual(self.register("13800000099", password)[0], 201)
        self.assertEqual(self.request("/api/auth/login", {"phone": "13800000099", "password": password})[0], 200)


if __name__ == "__main__":
    unittest.main()
