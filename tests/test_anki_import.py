import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import anki_import
import app


class DeckImportTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"STUDY_DB": str(self.folder / "study.sqlite3")}))
        app.setup()

    def test_reimport_preserves_reviews_and_browse_includes_reviewed_cards(self):
        source = b'#separator:Tab\n#html:false\n#columns:Front\tBack\n"<Q>"\t"An answer"\n'
        first = app.save_deck("Biology", anki_import.read_deck(io.BytesIO(source), ".tsv", self.folder))
        self.assertEqual(first["imported"], 1)
        deck_id = str(first["id"])
        card, = app.review_cards(deck_id, 12)
        self.assertEqual(card["front"], "&lt;Q&gt;")
        app.review(card["id"], {"rating": "good", "reviews": 0})
        second = app.save_deck("Biology", anki_import.read_deck(io.BytesIO(source), ".tsv", self.folder))
        self.assertEqual((second["imported"], second["existing"]), (0, 1))
        self.assertEqual(app.review_cards(deck_id), [])
        browsed, = app.review_cards(deck_id, 12, practice=True)
        self.assertEqual(browsed["reviews"], 1)
        self.assertEqual(app.review_cards(deck_id, 12, str(card["id"]), practice=True), [])
        self.assertIn(b'"<Q>"\t"An answer"', app.anki_export())

    def test_sanitizer_blocks_script_attributes_and_external_media(self):
        attempted = []
        def asset(source, tag):
            attempted.append((source, tag))
            return None
        html = anki_import.clean_html(
            '<script>alert(1)</script><b onclick="x()">Safe</b>'
            '<img src="https://example.com/pixel"><iframe src="https://example.com">hidden</iframe>',
            asset,
        )
        self.assertEqual(html, "<b>Safe</b>[Media unavailable]")
        self.assertEqual(attempted, [("https://example.com/pixel", "img")])

    def test_large_zip64_package_imports_more_than_five_thousand_cards(self):
        collection = self.folder / "large-collection.sqlite3"
        db = sqlite3.connect(collection)
        try:
            db.executescript("""
                CREATE TABLE col (models TEXT);
                CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, flds TEXT, tags TEXT);
                CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, ord INTEGER);
            """)
            model = {"flds": [{"ord": 0, "name": "Front"}, {"ord": 1, "name": "Back"}],
                     "tmpls": [{"ord": 0, "qfmt": "{{Front}}", "afmt": "{{Back}}"}]}
            db.execute("INSERT INTO col VALUES (?)", (json.dumps({"1": model}),))
            db.executemany("INSERT INTO notes VALUES (?, ?, 1, ?, '')", (
                (number, str(number), f"Question {number}\x1fAnswer") for number in range(5001)
            ))
            db.execute("INSERT INTO cards SELECT id, id, 0 FROM notes")
            db.commit()
        finally:
            db.close()

        package = self.folder / "large.apkg"
        with ZipFile(package, "w") as archive:
            archive.write(collection, "collection.anki2")
            for number in range(66_000):
                archive.writestr(str(number), b"")
        with package.open("rb") as upload:
            deck = anki_import.read_deck(upload, ".apkg", self.folder)
        self.assertEqual(app.save_deck("Large deck", deck)["imported"], 5001)

    @unittest.skipUnless(anki_import.zstd is not None, "Python 3.14 required for modern Anki packages")
    def test_modern_collection_uses_cloze_and_ignores_unused_fields(self):
        db_path = self.folder / "fixture.sqlite3"
        db = sqlite3.connect(db_path)
        try:
            db.create_collation("unicase", lambda a, b: (a > b) - (a < b))
            db.executescript("""
                CREATE TABLE notetypes (id INTEGER PRIMARY KEY, config BLOB);
                CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT COLLATE unicase,
                                     PRIMARY KEY(ntid, ord)) WITHOUT ROWID;
                CREATE TABLE templates (ntid INTEGER, ord INTEGER, config BLOB);
                CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, flds TEXT, tags TEXT);
                CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, ord INTEGER);
                INSERT INTO notetypes VALUES (1, X'0801');
                INSERT INTO fields VALUES (1, 0, 'Text'), (8, 0, 'Unused');
                INSERT INTO notes VALUES (1, 'note', 1, 'This is {{c1::hidden::hint}}.', '');
                INSERT INTO cards VALUES (1, 1, 0);
            """)
            template = b"{{cloze:Text}}"
            db.execute("INSERT INTO templates VALUES (1, 0, ?)",
                       (bytes([10, len(template)]) + template + bytes([18, len(template)]) + template,))
            db.commit()
        finally:
            db.close()
        upload = io.BytesIO()
        with ZipFile(upload, "w") as archive:
            archive.writestr("collection.anki21b", anki_import.zstd.compress(db_path.read_bytes()))
        imported = anki_import.read_deck(upload, ".apkg", self.folder)
        self.assertEqual(imported.cards, [("note:0", "This is <mark>[hint]</mark>.", "This is <mark>hidden</mark>.")])
