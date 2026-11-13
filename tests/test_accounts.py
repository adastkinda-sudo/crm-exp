"""Account credential, session, concurrency and recommendation persistence checks."""

import json
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import database
import server


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db_patch = patch.object(database, "DB_FILE", root / "test.sqlite3")
        self.credentials_patch = patch.object(database, "CREDENTIAL_FILE", root / ".credentials.json")
        self.db_patch.start(); self.credentials_patch.start()
        self.addCleanup(self.db_patch.stop); self.addCleanup(self.credentials_patch.stop)
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

    def test_seed_credentials_are_independent_and_old_format_is_migrated(self):
        credentials = json.loads(database.CREDENTIAL_FILE.read_text())
        passwords = credentials["users"]
        self.assertEqual(len(set(passwords.values())), len(passwords))
        for username, password in passwords.items():
            self.assertIsNotNone(database.get_user_by_credentials(username, password))
            for other in passwords:
                if other != username:
                    self.assertIsNone(database.get_user_by_credentials(other, password))
        database.CREDENTIAL_FILE.write_text(json.dumps({"customers": {u: "old" for u in passwords if u != "admin"}, "admin": "old"}))
        database.init_db()
        migrated = json.loads(database.CREDENTIAL_FILE.read_text())["users"]
        self.assertNotEqual(migrated["lin"], "old")
        self.assertIsNone(database.get_user_by_credentials("lin", "old"))
        self.assertIsNotNone(database.get_user_by_credentials("lin", migrated["lin"]))

    def test_missing_seed_credential_is_generated(self):
        credentials = json.loads(database.CREDENTIAL_FILE.read_text())
        missing = credentials["users"].pop("chen")
        database.CREDENTIAL_FILE.write_text(json.dumps(credentials))
        database.init_db()
        updated = json.loads(database.CREDENTIAL_FILE.read_text())["users"]
        self.assertIn("chen", updated)
        self.assertNotEqual(updated["chen"], missing)
        self.assertIsNotNone(database.get_user_by_credentials("chen", updated["chen"]))

    def test_concurrent_registration_returns_one_created_and_conflicts(self):
        # Stay within the server's default listen backlog so only the database race is exercised.
        workers = 5
        barrier = threading.Barrier(workers)
        results = []
        def register():
            barrier.wait()
            try:
                results.append(self.request("/api/auth/register", {"name": "并发客户", "username": "same_name", "password": secrets.token_urlsafe(8)})[0])
            except Exception as error:  # surface transport failures as test data
                results.append(repr(error))
        threads = [threading.Thread(target=register) for _ in range(workers)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(results, key=str), [201] + [409] * (workers - 1))

    def test_expired_session_returns_unauthorized_without_waiting(self):
        password = secrets.token_urlsafe(10)
        database.create_customer_account("客户", "expiring", password, 2, 365, 10000)
        status, payload = self.request("/api/auth/login", {"username": "expiring", "password": password})
        token = payload["token"]
        with patch.object(server.time, "time", return_value=server.SESSIONS[token][1] + 1):
            self.assertEqual(self.request("/api/auth/me", token=token)[0], 401)
        self.assertNotIn(token, server.SESSIONS)

    def test_repeated_recommendation_request_does_not_add_logs(self):
        credentials = json.loads(database.CREDENTIAL_FILE.read_text())["users"]
        status, payload = self.request("/api/auth/login", {"username": "lin", "password": credentials["lin"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/recommendations", token=payload["token"])[0], 200)
        first = database.get_diagnostics()["counts"]["recommendations"]
        self.assertEqual(self.request("/api/recommendations", token=payload["token"])[0], 200)
        self.assertEqual(database.get_diagnostics()["counts"]["recommendations"], first)

    def test_seed_plan_status_drives_homepage(self):
        credentials = json.loads(database.CREDENTIAL_FILE.read_text())["users"]
        for username, expected in (("lin", True), ("chen", True), ("wang", True), ("zhao", True), ("zhou", False)):
            status, payload = self.request("/api/auth/login", {"username": username, "password": credentials[username]})
            self.assertEqual(status, 200)
            status, home = self.request("/api/recommendations", token=payload["token"])
            self.assertEqual(status, 200)
            self.assertEqual(home["plan"]["profile_complete"], expected)
            if expected:
                self.assertTrue(home["items"], username)
            else:
                self.assertEqual(home["items"], [])


if __name__ == "__main__":
    unittest.main()
