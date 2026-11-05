import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class ReviewTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"STUDY_DB": str(Path(directory) / "review.sqlite3")}))
        self.now = self.enterContext(patch.object(app.time, "time", return_value=100_000))
        app.setup()
        result = app.save_chat_cards({"name": "Recall", "cards": [{"front": "Question", "back": "Answer"}]})
        self.deck = str(result["id"])
        self.card = app.review_cards(self.deck)[0]["id"]

    def grade(self, rating, expected_delay, expected_days, expected_step, expected_ease):
        card, = app.review_cards(self.deck)
        self.assertEqual(card["intervals"][rating], expected_delay)
        app.review(self.card, {"rating": rating, "reviews": card["reviews"]})
        due = self.now.return_value + expected_delay
        with app.database() as db:
            saved = db.execute("SELECT due, days, learning_step, ease, reviews FROM cards WHERE id=?",
                               (self.card,)).fetchone()
        self.assertEqual(tuple(saved), (due, expected_days, expected_step, expected_ease, card["reviews"] + 1))
        self.assertEqual(app.review_cards(self.deck, session=True), {"cards": [], "wait": expected_delay})
        self.now.return_value = due

    def test_learning_and_relearning_keep_ease_between_graduated_reviews(self):
        self.grade("again", 60, 0, "first", 250)
        self.grade("hard", 360, 0, "first", 250)
        self.grade("good", 600, 0, "second", 250)
        self.grade("hard", 600, 0, "second", 250)
        self.grade("good", app.DAY, 1, None, 250)
        self.grade("again", 600, 0, "relearning", 230)
        self.grade("again", 600, 0, "relearning", 230)
        self.grade("hard", 900, 0, "relearning", 230)
        self.grade("easy", 2 * app.DAY, 2, None, 230)

    def test_easy_graduates_and_passing_reviews_use_the_current_ease(self):
        self.grade("easy", 4 * app.DAY, 4, None, 250)
        self.grade("hard", 5 * app.DAY, 5, None, 235)
        self.grade("good", 12 * app.DAY, 12, None, 235)
        self.grade("easy", 37 * app.DAY, 37, None, 250)
        browsed, = app.review_cards(self.deck, practice=True)
        self.assertNotIn("intervals", browsed)
        self.assertEqual(browsed["reviews"], 4)
        with self.assertRaisesRegex(app.BadRequest, "another device"):
            app.review(self.card, {"rating": "hard", "reviews": 3})

    def test_upgrade_preserves_due_and_review_counts(self):
        due = self.now.return_value + 120
        with app.database() as db:
            db.execute("ALTER TABLE cards DROP COLUMN learning_step")
            db.execute("ALTER TABLE cards DROP COLUMN ease")
            db.execute("UPDATE cards SET days=10, due=?, reviews=3 WHERE id=?", (due, self.card))
            db.execute("""
                INSERT INTO cards (note, front, back, evidence, question_key, state, due, days, reviews)
                VALUES (?, 'Missed', 'Answer', '', 'missed', 'active', ?, 0, 2)
            """, (self.deck, due))
        app.setup()
        app.setup()
        self.assertEqual(app.review_cards(self.deck, session=True), {"cards": [], "wait": 120})
        self.now.return_value = due
        missed, graduated = app.review_cards(self.deck, limit=2)
        self.assertEqual((missed["front"], missed["reviews"]), ("Missed", 2))
        self.assertEqual(missed["intervals"], {"again": 600, "hard": 900, "good": app.DAY, "easy": 2 * app.DAY})
        self.assertEqual(graduated["reviews"], 3)
        self.assertEqual(graduated["intervals"]["good"], 25 * app.DAY)
