from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from kkoma_slack.storage import StateStore


class NamespaceIsolationTest(unittest.TestCase):
    def test_same_channel_day_isolated_by_game(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            store.ensure_game("kkoma", "T1", "C1", 5, "U1")
            store.ensure_game("sema", "T1", "C1", 5, "U1")
            store.record_guess("kkoma", "T1", "C1", 5, "U1", "보성", "사과", 0.5, "1000위 이상", False)
            store.record_guess("sema", "T1", "C1", 5, "U1", "보성", "apple", 0.9, "10위", False)

            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 5), 1)
            self.assertEqual(store.guess_count("sema", "T1", "C1", 5), 1)
            self.assertEqual(store.guesses("kkoma", "T1", "C1", 5)[0].word, "사과")
            self.assertEqual(store.guesses("sema", "T1", "C1", 5)[0].word, "apple")

    def test_is_active_and_release(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            self.assertFalse(store.is_active("kkoma", "T1", "C1", 5))
            store.ensure_game("kkoma", "T1", "C1", 5, "U1")
            self.assertTrue(store.is_active("kkoma", "T1", "C1", 5))
            store.reveal_answer("kkoma", "T1", "C1", 5)
            self.assertFalse(store.is_active("kkoma", "T1", "C1", 5))

    def test_is_active_cleared_on_solve(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            store.ensure_game("sema", "T1", "C1", 5, "U1")
            store.record_guess("sema", "T1", "C1", 5, "U1", "n", "apple", 1.0, "정답!", True)
            self.assertFalse(store.is_active("sema", "T1", "C1", 5))


OLD_SCHEMA = """
CREATE TABLE games (
    team_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    started_by TEXT NOT NULL,
    started_at TEXT NOT NULL,
    solved_by TEXT,
    solved_at TEXT,
    answer_revealed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, channel_id, day)
);
CREATE TABLE guesses (
    team_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    word TEXT NOT NULL,
    similarity REAL NOT NULL,
    rank TEXT NOT NULL,
    is_answer INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (team_id, channel_id, day, word)
);
CREATE TABLE hints (
    team_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    level TEXT NOT NULL,
    rank INTEGER NOT NULL,
    word TEXT NOT NULL,
    similarity REAL NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (team_id, channel_id, day, level)
);
"""


class MigrationTest(unittest.TestCase):
    def _seed_old_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO games VALUES ('T1','C1',5,'U1','t1',NULL,NULL,0)"
        )
        conn.execute(
            "INSERT INTO guesses VALUES ('T1','C1',5,'U1','보성','사과',0.5,'1000위 이상',0,'t2')"
        )
        conn.execute(
            "INSERT INTO hints VALUES ('T1','C1',5,'weak',800,'배',0.4,'U1','t3')"
        )
        conn.commit()
        conn.close()

    def test_migration_preserves_existing_data_as_kkoma(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.db"
            self._seed_old_db(path)

            store = StateStore(path)  # triggers migration

            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 5), 1)
            guesses = store.guesses("kkoma", "T1", "C1", 5)
            self.assertEqual(guesses[0].word, "사과")
            self.assertEqual(guesses[0].user_name, "보성")
            self.assertTrue(store.has_game("kkoma", "T1", "C1", 5))
            self.assertIsNotNone(store.hint("kkoma", "T1", "C1", 5, "weak"))

            # game column now present
            conn = sqlite3.connect(path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(guesses)").fetchall()}
            conn.close()
            self.assertIn("game", cols)

    def test_migration_is_idempotent(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.db"
            self._seed_old_db(path)
            StateStore(path)
            store = StateStore(path)  # second init must not lose data or error
            self.assertEqual(store.guess_count("kkoma", "T1", "C1", 5), 1)

    def test_fresh_db_has_game_column(self):
        with TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "state.db")
            store.ensure_game("kkoma", "T1", "C1", 0, "U1")
            self.assertTrue(store.has_game("kkoma", "T1", "C1", 0))


if __name__ == "__main__":
    unittest.main()
