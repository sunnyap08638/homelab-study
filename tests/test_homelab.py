import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import wake


class NotesTest(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"STUDY_DB": str(Path(temporary) / "test.sqlite3")}))
        app.setup()
        self.note = {"source": "obsidian", "name": "cluster.md", "body": "A Service has a stable IP."}
        app.import_note(self.note)
        self.card = {"front": "What has a stable IP?", "back": "A Service.", "evidence": self.note["body"]}
        self.worker = Mock()
        self.worker.chat.return_value = {"cards": [self.card]}

    def test_draft_review_export_and_duplicate_detection(self):
        request = {"revision": 1, "section": 0}
        self.assertEqual(app.generate(self.worker, 1, request)["inserted"], 1)
        self.assertEqual(app.generate(self.worker, 1, request)["inserted"], 0)
        draft, = app.note_details(1)["drafts"]
        app.decide(draft["id"], {"action": "approve", **self.card})
        app.review(draft["id"], {"rating": "good", "reviews": 0})
        with self.assertRaises(app.BadRequest):
            app.review(draft["id"], {"rating": "good", "reviews": 0})
        self.assertIn(b'"What has a stable IP?"\t"A Service."', app.anki_export())

    def test_import_during_generation_rejects_stale_drafts(self):
        def update_note(_messages):
            app.import_note({**self.note, "body": "An updated note."})
            return {"cards": [self.card]}

        self.worker.chat.side_effect = update_note
        with self.assertRaisesRegex(app.BadRequest, "changed during generation"):
            app.generate(self.worker, 1, {"revision": 1, "section": 0})
        self.assertEqual(app.note_details(1)["drafts"], [])


class WakeTest(unittest.TestCase):
    def test_wake_packet(self):
        mac = bytes.fromhex("001122334455")
        self.assertEqual(wake.magic_packet("00-11-22-33-44-55"), b"\xff" * 6 + mac * 16)
        with self.assertRaises(ValueError):
            wake.magic_packet("00:11:22")

    def test_unreachable_worker_gets_only_one_packet_during_cooldown(self):
        worker = wake.Worker("http://127.0.0.1:11434", "qwen3.8:27b", "00:11:22:33:44:55", "192.0.2.255", 0)
        with patch.object(worker, "ready", return_value=False), patch.object(wake, "wake") as send:
            with self.assertRaisesRegex(wake.WorkerUnavailable, "did not become ready"):
                worker.chat([])
            with self.assertRaisesRegex(wake.WorkerUnavailable, "last wake attempt"):
                worker.chat([])
        send.assert_called_once_with("00:11:22:33:44:55", "192.0.2.255")
