import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import uploads


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = uploads.Uploads(self.folder / "staging")
        self.addCleanup(self.store.close)

    def test_retry_keeps_offset_after_an_interrupted_chunk(self):
        key = self.store.start(6)["id"]
        self.store.append(key, 0, 3, io.BytesIO(b"abc"))
        with self.assertRaisesRegex(uploads.UploadError, "connection ended"):
            self.store.append(key, 3, 3, io.BytesIO(b"d"))
        self.assertEqual(self.store.status(key)["offset"], 3)
        self.assertEqual(self.store.append(key, 0, 3, io.BytesIO(b"abc"))["offset"], 3)
        self.store.append(key, 3, 3, io.BytesIO(b"def"))
        upload = self.store.claim(key)
        self.assertEqual(upload.path.read_bytes(), b"abcdef")
        self.store.finish(upload)
        self.assertFalse(upload.path.exists())

    def test_accepts_the_configured_boundary_without_preallocating(self):
        with patch.object(uploads, "MAX_BYTES", 100_000_000_000), \
                patch("uploads.shutil.disk_usage", return_value=Mock(free=110_000_000_000)):
            key = self.store.start(100_000_000_000)["id"]
            self.assertEqual(self.store.get(key).path.stat().st_size, 0)
            with self.assertRaisesRegex(uploads.UploadError, "100 GB"):
                self.store.start(100_000_000_001)
            with self.assertRaisesRegex(uploads.UploadError, "enough free disk space"):
                self.store.start(11_000_000_000)

    def test_temporary_source_is_deleted_before_inference_and_only_cards_are_saved(self):
        self.enterContext(patch.dict(os.environ, {"STUDY_DB": str(self.folder / "study.sqlite3")}))
        app.setup()
        source = b"Private example text about soil."
        key = self.store.start(len(source))["id"]
        self.store.append(key, 0, len(source), io.BytesIO(source))
        path = self.store.get(key).path

        def generate(messages):
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(messages[-1]["content"])["source"], source.decode())
            return {"cards": [{"front": "What is topsoil?", "back": "The upper soil layer.",
                               "evidence": source.decode()}]}

        handler = object.__new__(app.Handler)
        handler.server = Mock(uploads=self.store)
        handler.server.worker.chat.side_effect = generate
        result = handler.generate_chat({"directions": "Explain soil", "source": "", "upload": key, "suffix": ".txt"})
        with app.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
        app.save_chat_cards({"name": "Soil", "cards": result["cards"]})
        with app.database() as db:
            self.assertEqual(db.execute("SELECT source, body FROM notes").fetchone()[:], ("chat", ""))
            self.assertEqual(db.execute("SELECT evidence FROM cards").fetchone()[0], "")
        self.assertIn(b"The upper soil layer.", app.anki_export())
