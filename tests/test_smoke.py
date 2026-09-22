"""Smoke tests: boot the real server and use every endpoint.

These run against the standard library only, so they work in CI with no install
step — the same promise the app makes to its users.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the app at a throwaway folder before it is imported.
_TMP = tempfile.mkdtemp(prefix="nanolaama-tests-")
os.environ["NANOLAAMA_HOME"] = _TMP

from nanolaama.httpbase import free_port  # noqa: E402
from nanolaama.server import create_app  # noqa: E402


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.port = free_port(8791)
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(
            target=cls.app.serve,
            kwargs={"host": "127.0.0.1", "port": cls.port, "open_browser": False, "quiet": True},
            daemon=True,
        )
        cls.thread.start()
        # Wait for the socket to answer before the first test runs.
        for _ in range(80):
            try:
                cls.get("/api/health")
                return
            except Exception:
                threading.Event().wait(0.05)
        raise RuntimeError("the test server never came up")

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()

    # -- helpers ----------------------------------------------------------
    @classmethod
    def get(cls, path, raw=False):
        with urllib.request.urlopen(cls.base + path, timeout=10) as response:
            body = response.read()
        return body if raw else json.loads(body.decode("utf-8"))

    @classmethod
    def post(cls, path, payload=None, raw=False, expect_error=False):
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if not expect_error:
                raise
            body = exc.read()
        return body if raw else json.loads(body.decode("utf-8"))

    @classmethod
    def post_bytes(cls, path, data):
        """Raw-body POST. Returns the decoded JSON whether it succeeded or not."""
        request = urllib.request.Request(cls.base + path, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
        return json.loads(body.decode("utf-8"))

    @classmethod
    def stream_post(cls, path, payload=None):
        """POST an SSE endpoint and return the raw text of the stream."""
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")


class TestBasics(ServerTestCase):
    def test_health(self):
        data = self.get("/api/health")
        self.assertTrue(data["ok"])
        self.assertEqual(data["app"], "nanoLaama")

    def test_status_has_hardware_and_engines(self):
        data = self.get("/api/status")
        self.assertIn("hardware", data)
        self.assertIn("engines", data)
        self.assertIsInstance(data["engines"], list)
        self.assertIn("memory_budget_gb", data)
        self.assertIn("native", data)

    def test_index_is_served(self):
        body = self.get("/", raw=True).decode("utf-8")
        self.assertIn("nanoLaama", body)
        self.assertIn("/app.js", body)

    def test_static_assets(self):
        for path, needle in (("/style.css", "--accent"), ("/app.js", "mdToHtml")):
            body = self.get(path, raw=True).decode("utf-8")
            self.assertIn(needle, body)

    def test_unknown_page_does_not_leak_files(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/../../etc/passwd", raw=True)
        self.assertIn(caught.exception.code, (400, 404))

    def test_unknown_api_path_returns_json_error(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/does-not-exist")


class TestSettingsAndChats(ServerTestCase):
    def test_settings_round_trip(self):
        saved = self.post("/api/settings", {"temperature": 0.3, "model": "test-model"})
        self.assertEqual(saved["temperature"], 0.3)
        again = self.get("/api/settings")
        self.assertEqual(again["model"], "test-model")
        # Put it back so other tests see defaults.
        self.post("/api/settings", {"temperature": 0.7, "model": ""})

    def test_chat_saved_and_deleted(self):
        chat = {"id": "test-chat-1", "title": "Hello", "messages": [{"role": "user", "content": "hi"}]}
        result = self.post("/api/chats", chat)
        self.assertTrue(any(item["id"] == "test-chat-1" for item in result["chats"]))
        listed = self.get("/api/chats")
        self.assertTrue(any(item["id"] == "test-chat-1" for item in listed["chats"]))

        request = urllib.request.Request(self.base + "/api/chats/test-chat-1", method="DELETE")
        with urllib.request.urlopen(request, timeout=10) as response:
            after = json.loads(response.read().decode("utf-8"))
        self.assertFalse(any(item["id"] == "test-chat-1" for item in after["chats"]))

    def test_chat_without_an_engine_yields_a_clear_error(self):
        """Chat errors travel inside the stream, so the UI can show them in place."""
        text = self.stream_post("/api/chat", {"engine": "ollama", "model": "x", "messages": []})
        events = [
            json.loads(line[5:])
            for line in text.splitlines()
            if line.startswith("data:") and line.strip() != "data: [DONE]"
        ]
        self.assertTrue(events, "the endpoint sent nothing at all")
        self.assertTrue(any(event.get("type") == "error" for event in events), events)
        self.assertTrue(any("message" in event for event in events if event.get("type") == "error"))


class TestUploads(ServerTestCase):
    def test_chunked_upload_round_trip(self):
        start = self.post("/api/upload/start", {"name": "my model.gguf"})
        upload_id = start["id"]
        self.assertEqual(start["name"], "my model.gguf")

        first = self.post_bytes("/api/upload/chunk?id=%s&offset=0" % upload_id, b"a" * 100)
        self.assertEqual(first["received"], 100)
        self.post_bytes("/api/upload/chunk?id=%s&offset=100" % upload_id, b"b" * 50)

        done = self.post("/api/upload/finish", {"id": upload_id})
        self.assertEqual(done["name"], "my model.gguf")
        self.assertTrue(os.path.isfile(done["path"]))
        self.assertEqual(os.path.getsize(done["path"]), 150)

    def test_out_of_order_chunk_is_rejected(self):
        start = self.post("/api/upload/start", {"name": "thing.bin"})
        data = self.post_bytes("/api/upload/chunk?id=%s&offset=999" % start["id"], b"x")
        self.assertIn("error", data)

    def test_dangerous_filename_is_neutralised(self):
        start = self.post("/api/upload/start", {"name": "../../evil.sh"})
        self.assertNotIn("/", start["name"])
        self.assertNotIn("..", start["name"])

    def test_files_listing(self):
        data = self.get("/api/files")
        self.assertIn("files", data)
        self.assertIn("models", data["folder"])


class TestNativeEngine(unittest.TestCase):
    """The nanollama.c bridge, exercised without needing the C project present."""

    def test_find_repo_in_a_folder_that_is_not_the_project(self):
        """A saved path that exists but is the wrong folder must be reported, not crashed on."""
        from nanolaama import native
        empty = os.path.join(_TMP, "empty-folder")
        os.makedirs(empty, exist_ok=True)
        found = native.find_repo(empty)
        self.assertFalse(found["found"])
        self.assertTrue(found["how"])

    def test_find_repo_with_a_nonsense_path_still_answers(self):
        from nanolaama import native
        found = native.find_repo(os.path.join(_TMP, "no-such-place-xyz"))
        self.assertIn("found", found)
        self.assertIn("how", found)

    def test_summary_shape(self):
        from nanolaama import native
        summary = native.summary()
        for key in ("found", "how", "path", "built", "can_build", "models", "running", "engine_home"):
            self.assertIn(key, summary)

    def test_stop_when_not_running_is_harmless(self):
        from nanolaama import native
        result = native.stop()
        self.assertFalse(result["stopped"])


class TestEngineReplyParsing(unittest.TestCase):
    """Small C engines write sloppy JSON. The app must cope, not sulk."""

    # A reply in the shape nanollama.c's server.c produces: the model's text
    # contains unescaped newlines AND quotes, so this is not valid JSON.
    BROKEN = (
        '{\n  "id": "chatcmpl-nanollama",\n  "object": "chat.completion",\n'
        '  "choices": [{\n    "index": 0,\n    "message": {\n      "role": "assistant",\n'
        '      "content": "Once upon a time\nthere was a girl. She said "hello" to a bear."\n'
        '    },\n    "finish_reason": "stop"\n  }],\n'
        '  "usage": {"prompt_tokens": 4, "completion_tokens": 12, "total_tokens": 16}\n}'
    )

    def test_broken_reply_is_recovered(self):
        from nanolaama import backends
        # It really is invalid JSON, or this test proves nothing.
        with self.assertRaises(json.JSONDecodeError):
            json.loads(self.BROKEN)

        data = backends._parse_reply(self.BROKEN)
        self.assertIsNotNone(data)
        self.assertTrue(data["_salvaged"])
        text = backends._extract_openai_text(data)
        self.assertIn("Once upon a time", text)
        self.assertIn("hello", text)
        self.assertIn("\n", text)          # the newline survived
        self.assertNotIn('"finish_reason"', text)
        self.assertNotIn("usage", text)

    def test_valid_reply_is_not_touched(self):
        from nanolaama import backends
        good = json.dumps({"choices": [{"message": {"content": "A tidy answer."}}]})
        data = backends._parse_reply(good)
        self.assertNotIn("_salvaged", data)
        self.assertEqual(backends._extract_openai_text(data), "A tidy answer.")

    def test_streaming_chunk_shape(self):
        from nanolaama import backends
        chunk = json.dumps({"choices": [{"delta": {"content": "Hel"}}]})
        self.assertEqual(backends._extract_openai_text(json.loads(chunk)), "Hel")

    def test_unreadable_body_returns_none(self):
        from nanolaama import backends
        self.assertIsNone(backends._parse_reply("this is not JSON at all"))


class TestHardware(unittest.TestCase):
    def test_profile_has_sane_values(self):
        from nanolaama import hardware
        info = hardware.profile()
        self.assertGreaterEqual(info["cpu_cores"], 1)
        self.assertGreaterEqual(info["ram_gb"], 0)
        self.assertTrue(hardware.describe().endswith("."))
        self.assertGreater(hardware.memory_budget_gb(), 0)

    def test_recommendations_marked_for_this_machine(self):
        from nanolaama import backends
        catalog = backends.recommended_models()
        self.assertGreater(len(catalog), 5)
        for entry in catalog:
            self.assertIn("fits", entry)
            self.assertIn("blurb", entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
