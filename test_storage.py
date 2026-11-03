import os
import tempfile
import socket
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient
from app import create_app


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "STORAGE_TOKEN": "test-secret-" + "a" * 48,
            "STORAGE_DIRECTORY": self.directory.name,
            "MAX_UPLOAD_BYTES": "2048",
        })
        self.env.start()
        self.auth = {"Authorization": "Bearer " + os.environ["STORAGE_TOKEN"]}
        self.client = TestClient(create_app())

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.directory.cleanup()

    def test_missing_and_wrong_auth_cannot_read_write_delete(self):
        for method in ("get", "put", "delete"):
            for headers in ({}, {"Authorization": "Bearer incorrect"}):
                with self.subTest(method=method, headers=headers):
                    self.assertEqual(getattr(self.client, method)("/objects/test", headers=headers).status_code, 401)

    def test_binary_round_trip_encoded_key_and_immutable_original(self):
        url = "/objects/" + quote("archive/folder/a b%25_日本.pdf", safe="")
        data = b"%PDF-1.7\x00\xff\x01"
        self.assertEqual(self.client.put(url, headers=self.auth, content=data).status_code, 201)
        self.assertEqual(self.client.put(url, headers=self.auth, content=b"replace").status_code, 409)
        response = self.client.get(url, headers=self.auth)
        self.assertEqual(response.content, data)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_documents_survive_application_restart(self):
        self.client.put("/objects/persist", headers=self.auth, content=b"keep me")
        with TestClient(create_app()) as restarted:
            self.assertEqual(restarted.get("/objects/persist", headers=self.auth).content, b"keep me")

    def test_delete_is_idempotent_and_missing_object_is_404(self):
        self.client.put("/objects/delete", headers=self.auth, content=b"test")
        for _ in range(2):
            self.assertEqual(self.client.delete("/objects/delete", headers=self.auth).status_code, 204)
        self.assertEqual(self.client.get("/objects/delete", headers=self.auth).status_code, 404)

    def test_oversized_request_does_not_leave_partial_objects(self):
        self.assertEqual(self.client.put("/objects/large", headers=self.auth, content=b"x" * 2049).status_code, 413)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_streamed_upload_size_limit_and_cleanup(self):
        def chunks():
            yield b"a" * 1024
            yield b"b" * 1025
        self.assertEqual(self.client.put("/objects/stream", headers=self.auth, content=chunks()).status_code, 413)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_real_http_protocol_used_by_application(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, PORT=str(port))
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("app.py"))], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    with urlopen(base + "/health", timeout=1) as response:
                        self.assertEqual(response.status, 200)
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail("Bridge process stopped before becoming healthy")
                    time.sleep(0.1)
            else:
                self.fail("Bridge did not become healthy")
            url = base + "/objects/" + quote("archive/document/file.pdf", safe="")
            for method, data, status in (("PUT", b"%PDF-test", 201), ("GET", None, 200), ("DELETE", None, 204)):
                with urlopen(Request(url, method=method, data=data, headers=self.auth), timeout=3) as response:
                    self.assertEqual(response.status, status)
                    if method == "GET":
                        self.assertEqual(response.read(), b"%PDF-test")
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_opaque_keys_cannot_escape_storage_directory(self):
        self.client.put("/objects/" + quote("../../outside.txt", safe=""), headers=self.auth, content=b"safe")
        files = list(Path(self.directory.name).iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, ".blob")

    def test_concurrent_create_only_has_one_winner(self):
        def upload(body):
            with TestClient(create_app()) as client:
                return client.put("/objects/race", headers=self.auth, content=body).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(executor.map(upload, [b"first", b"second"]))
        self.assertEqual(statuses, [201, 409])

    def test_health_and_missing_mount(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        with patch.dict(os.environ, {"STORAGE_DIRECTORY": str(Path(self.directory.name) / "missing")}):
            with self.assertRaises(OSError):
                with TestClient(create_app()):
                    pass

    def test_bad_configuration_is_rejected(self):
        for name, value in (("STORAGE_TOKEN", ""), ("STORAGE_DIRECTORY", ""), ("MAX_UPLOAD_BYTES", "0")):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                with self.assertRaises(RuntimeError):
                    create_app()


if __name__ == "__main__":
    unittest.main()
