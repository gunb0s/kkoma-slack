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

    def ensure_game(self, game: str, team_id: str, channel_id: str, day: int, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO games (
                    game, team_id, channel_id, day, started_by, started_at,
                    solved_by, solved_at, answer_revealed
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0)
                """,
                (game, team_id, channel_id, day, user_id, now_iso()),
            )

    def has_game(self, game: str, team_id: str, channel_id: str, day: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM games
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
                """,
                (game, team_id, channel_id, day),
            ).fetchone()
        return row is not None

    def is_active(self, game: str, team_id: str, channel_id: str, day: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM games
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
                  AND solved_by IS NULL AND answer_revealed = 0
                """,
                (game, team_id, channel_id, day),
            ).fetchone()
        return row is not None

    def record_guess(
        self,
        game: str,
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
                    game, team_id, channel_id, day, user_id, user_name,
                    word, similarity, rank, is_answer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game, team_id, channel_id, day, user_id, user_name,
                    word, similarity, rank, int(is_answer), now_iso(),
                ),
            )
            inserted = connection.total_changes > before
            if is_answer:
                connection.execute(
                    """
                    UPDATE games
                    SET solved_by = COALESCE(solved_by, ?), solved_at = COALESCE(solved_at, ?)
                    WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
                    """,
                    (user_id, now_iso(), game, team_id, channel_id, day),
                )
            return inserted

    def reveal_answer(self, game: str, team_id: str, channel_id: str, day: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE games
                SET answer_revealed = 1
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
                """,
                (game, team_id, channel_id, day),
            )

    def guesses(
        self, game: str, team_id: str, channel_id: str, day: int, limit: int | None = None
    ) -> list[StoredGuess]:
        sql = """
            SELECT user_id, user_name, word, similarity, rank, is_answer, created_at
            FROM guesses
            WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
            ORDER BY similarity DESC, created_at ASC
        """
        params: tuple[object, ...] = (game, team_id, channel_id, day)
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

    def guess_count(self, game: str, team_id: str, channel_id: str, day: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM guesses
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ?
                """,
                (game, team_id, channel_id, day),
            ).fetchone()
        return int(row[0])

    def duplicate_guess(
        self, game: str, team_id: str, channel_id: str, day: int, word: str
    ) -> StoredGuess | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, user_name, word, similarity, rank, is_answer, created_at
                FROM guesses
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ? AND word = ?
                """,
                (game, team_id, channel_id, day, word),
            ).fetchone()
        if row is None:
            return None
        return StoredGuess(row[0], row[1] or "", row[2], float(row[3]), row[4], bool(row[5]), row[6])

    def hint(self, game: str, team_id: str, channel_id: str, day: int, level: str) -> StoredHint | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT level, rank, word, similarity, created_by, created_at
                FROM hints
                WHERE game = ? AND team_id = ? AND channel_id = ? AND day = ? AND level = ?
                """,
                (game, team_id, channel_id, day, level),
            ).fetchone()
        if row is None:
            return None
        return StoredHint(row[0], int(row[1]), row[2], float(row[3]), row[4], row[5])

    def record_hint(
        self,
        game: str,
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
                    game, team_id, channel_id, day, level, rank, word, similarity, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (game, team_id, channel_id, day, level, rank, word, similarity, user_id, created_at),
            )
        stored = self.hint(game, team_id, channel_id, day, level)
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
                    game TEXT NOT NULL DEFAULT 'kkoma',
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    started_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    solved_by TEXT,
                    solved_at TEXT,
                    answer_revealed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (game, team_id, channel_id, day)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guesses (
                    game TEXT NOT NULL DEFAULT 'kkoma',
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
                    PRIMARY KEY (game, team_id, channel_id, day, word)
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(guesses)").fetchall()}
            if "user_name" not in columns:
                connection.execute("ALTER TABLE guesses ADD COLUMN user_name TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hints (
                    game TEXT NOT NULL DEFAULT 'kkoma',
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (game, team_id, channel_id, day, level)
                )
                """
            )
            self._migrate_add_game_column(connection)

    def _migrate_add_game_column(self, connection: sqlite3.Connection) -> None:
        plans = {
            "games": (
                """
                CREATE TABLE games_new (
                    game TEXT NOT NULL DEFAULT 'kkoma',
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    started_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    solved_by TEXT,
                    solved_at TEXT,
                    answer_revealed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (game, team_id, channel_id, day)
                )
                """,
                """
                INSERT INTO games_new (
                    game, team_id, channel_id, day, started_by, started_at,
                    solved_by, solved_at, answer_revealed
                )
                SELECT 'kkoma', team_id, channel_id, day, started_by, started_at,
                       solved_by, solved_at, answer_revealed
                FROM games
                """,
            ),
            "guesses": (
                """
                CREATE TABLE guesses_new (
                    game TEXT NOT NULL DEFAULT 'kkoma',
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
                    PRIMARY KEY (game, team_id, channel_id, day, word)
                )
                """,
                """
                INSERT INTO guesses_new (
                    game, team_id, channel_id, day, user_id, user_name,
                    word, similarity, rank, is_answer, created_at
                )
                SELECT 'kkoma', team_id, channel_id, day, user_id, user_name,
                       word, similarity, rank, is_answer, created_at
                FROM guesses
                """,
            ),
            "hints": (
                """
                CREATE TABLE hints_new (
                    game TEXT NOT NULL DEFAULT 'kkoma',
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (game, team_id, channel_id, day, level)
                )
                """,
                """
                INSERT INTO hints_new (
                    game, team_id, channel_id, day, level, rank, word, similarity, created_by, created_at
                )
                SELECT 'kkoma', team_id, channel_id, day, level, rank, word, similarity, created_by, created_at
                FROM hints
                """,
            ),
        }

        tables_to_migrate = [
            table
            for table in plans
            if "game" not in {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
        ]
        if not tables_to_migrate:
            return

        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN")
        try:
            for table in tables_to_migrate:
                create_sql, copy_sql = plans[table]
                connection.execute(create_sql)
                connection.execute(copy_sql)
                old_count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                new_count = connection.execute(f"SELECT COUNT(*) FROM {table}_new").fetchone()[0]
                if old_count != new_count:
                    raise RuntimeError(
                        f"migration row count mismatch for {table}: {old_count} != {new_count}"
                    )
                connection.execute(f"DROP TABLE {table}")
                connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
