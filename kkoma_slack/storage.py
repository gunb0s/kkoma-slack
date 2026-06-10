from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoredGuess:
    user_id: str
    user_name: str
    word: str
    similarity: float
    rank: str
    is_answer: bool
    created_at: str


@dataclass(frozen=True)
class StoredHint:
    level: str
    rank: int
    word: str
    similarity: float
    created_by: str
    created_at: str


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def ensure_game(self, team_id: str, channel_id: str, day: int, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO games (
                    team_id, channel_id, day, started_by, started_at, solved_by, solved_at, answer_revealed
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 0)
                """,
                (team_id, channel_id, day, user_id, now_iso()),
            )

    def record_guess(
        self,
        team_id: str,
        channel_id: str,
        day: int,
        user_id: str,
        user_name: str,
        word: str,
        similarity: float,
        rank: str,
        is_answer: bool,
    ) -> bool:
        with self._connect() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO guesses (
                    team_id, channel_id, day, user_id, user_name, word, similarity, rank, is_answer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (team_id, channel_id, day, user_id, user_name, word, similarity, rank, int(is_answer), now_iso()),
            )
            inserted = connection.total_changes > before
            if is_answer:
                connection.execute(
                    """
                    UPDATE games
                    SET solved_by = COALESCE(solved_by, ?), solved_at = COALESCE(solved_at, ?)
                    WHERE team_id = ? AND channel_id = ? AND day = ?
                    """,
                    (user_id, now_iso(), team_id, channel_id, day),
                )
            return inserted

    def reveal_answer(self, team_id: str, channel_id: str, day: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE games
                SET answer_revealed = 1
                WHERE team_id = ? AND channel_id = ? AND day = ?
                """,
                (team_id, channel_id, day),
            )

    def guesses(self, team_id: str, channel_id: str, day: int, limit: int | None = None) -> list[StoredGuess]:
        sql = """
            SELECT user_id, user_name, word, similarity, rank, is_answer, created_at
            FROM guesses
            WHERE team_id = ? AND channel_id = ? AND day = ?
            ORDER BY similarity DESC, created_at ASC
        """
        params: tuple[object, ...] = (team_id, channel_id, day)
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            StoredGuess(
                user_id=row[0],
                user_name=row[1] or "",
                word=row[2],
                similarity=float(row[3]),
                rank=row[4],
                is_answer=bool(row[5]),
                created_at=row[6],
            )
            for row in rows
        ]

    def guess_count(self, team_id: str, channel_id: str, day: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM guesses
                WHERE team_id = ? AND channel_id = ? AND day = ?
                """,
                (team_id, channel_id, day),
            ).fetchone()
        return int(row[0])

    def duplicate_guess(self, team_id: str, channel_id: str, day: int, word: str) -> StoredGuess | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, user_name, word, similarity, rank, is_answer, created_at
                FROM guesses
                WHERE team_id = ? AND channel_id = ? AND day = ? AND word = ?
                """,
                (team_id, channel_id, day, word),
            ).fetchone()
        if row is None:
            return None
        return StoredGuess(row[0], row[1] or "", row[2], float(row[3]), row[4], bool(row[5]), row[6])

    def hint(self, team_id: str, channel_id: str, day: int, level: str) -> StoredHint | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT level, rank, word, similarity, created_by, created_at
                FROM hints
                WHERE team_id = ? AND channel_id = ? AND day = ? AND level = ?
                """,
                (team_id, channel_id, day, level),
            ).fetchone()
        if row is None:
            return None
        return StoredHint(row[0], int(row[1]), row[2], float(row[3]), row[4], row[5])

    def record_hint(
        self,
        team_id: str,
        channel_id: str,
        day: int,
        level: str,
        rank: int,
        word: str,
        similarity: float,
        user_id: str,
    ) -> StoredHint:
        created_at = now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO hints (
                    team_id, channel_id, day, level, rank, word, similarity, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (team_id, channel_id, day, level, rank, word, similarity, user_id, created_at),
            )
        stored = self.hint(team_id, channel_id, day, level)
        if stored is None:
            raise RuntimeError("failed to store hint")
        return stored

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS games (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    started_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    solved_by TEXT,
                    solved_at TEXT,
                    answer_revealed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (team_id, channel_id, day)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guesses (
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
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(guesses)").fetchall()}
            if "user_name" not in columns:
                connection.execute("ALTER TABLE guesses ADD COLUMN user_name TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hints (
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
                )
                """
            )
