from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
import json
import pickle
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np


KST = ZoneInfo("Asia/Seoul")
FIRST_DAY = date(2022, 4, 1)
NUM_SECRETS = 4650

ANSWER_RANK = "정답!"
OUT_OF_RANK = "1000위 이상"


class EngineError(Exception):
    pass


class MissingDataError(EngineError):
    pass


class UnknownWordError(EngineError):
    pass


@dataclass(frozen=True)
class GuessResult:
    day: int
    guess: str
    similarity: float
    rank: str
    is_answer: bool


@dataclass(frozen=True)
class TopScore:
    rank: int
    word: str
    similarity: float


def today_puzzle(now: datetime | None = None) -> int:
    current = now or datetime.now(tz=KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    current_date = current.astimezone(KST).date()
    return (current_date - FIRST_DAY).days % NUM_SECRETS


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    return float(vec1.dot(vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2)))


class SelfHostedSemantleEngine:
    def __init__(self, data_dir: Path, allow_score_only: bool = False) -> None:
        self.data_dir = data_dir
        self.allow_score_only = allow_score_only
        self.secrets = self._load_secrets()
        self._valid_nearest_words: list[str] | None = None
        self._valid_nearest_vecs: np.ndarray | None = None
        self._nearest_cache: dict[int, dict[str, tuple[Any, float]]] = {}

    def today(self) -> int:
        return today_puzzle()

    def answer(self, day: int | None = None) -> str:
        return self.secrets[(self.today() if day is None else day) % len(self.secrets)]

    def guess(self, word: str, day: int | None = None) -> GuessResult:
        puzzle_day = (self.today() if day is None else day) % len(self.secrets)
        secret = self.secrets[puzzle_day]

        nearest = self._nearest_words(puzzle_day, secret)
        if word in nearest:
            rank, similarity = nearest[word]
        else:
            similarity = self._similarity(secret, word)
            rank = OUT_OF_RANK

        is_answer = word == secret
        if is_answer:
            rank = ANSWER_RANK
            similarity = 1.0

        return GuessResult(
            day=puzzle_day,
            guess=word,
            similarity=float(similarity),
            rank=str(rank),
            is_answer=is_answer,
        )

    def top_scores(self, day: int | None = None) -> list[TopScore]:
        puzzle_day = (self.today() if day is None else day) % len(self.secrets)
        secret = self.secrets[puzzle_day]
        nearest = self._nearest_words(puzzle_day, secret)
        scores = []
        for word, (rank, similarity) in nearest.items():
            if word == secret or not isinstance(rank, int):
                continue
            scores.append(TopScore(rank=rank, word=word, similarity=float(similarity)))
        return sorted(scores, key=lambda score: score.rank)

    def _load_secrets(self) -> list[str]:
        path = self.data_dir / "secrets.txt"
        if not path.exists():
            raise MissingDataError(f"missing {path}")
        secrets = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(secrets) < NUM_SECRETS:
            raise MissingDataError(f"{path} must contain {NUM_SECRETS} words")
        return secrets

    def _nearest_words(self, day: int, secret: str) -> dict[str, tuple[Any, float]]:
        if day in self._nearest_cache:
            return self._nearest_cache[day]

        path = self.data_dir / "near" / f"{day}.dat"
        if path.exists():
            with path.open("rb") as f:
                nearest = pickle.load(f)
            self._nearest_cache[day] = nearest
            return nearest

        if self.allow_score_only:
            return {}

        nearest = self._dump_nearest(day, secret)
        self._nearest_cache[day] = nearest
        return nearest

    def _load_valid_nearest(self) -> tuple[list[str], np.ndarray]:
        if self._valid_nearest_words is not None and self._valid_nearest_vecs is not None:
            return self._valid_nearest_words, self._valid_nearest_vecs

        path = self.data_dir / "valid_nearest.dat"
        if not path.exists():
            raise MissingDataError(
                "data/valid_nearest.dat is missing. Run ./scripts/bootstrap_data.sh first, "
                "or set KKOMA_ENGINE_MODE=remote for a quick non-self-hosted smoke test."
            )

        with path.open("rb") as f:
            words, vecs = pickle.load(f)
        self._valid_nearest_words = words
        self._valid_nearest_vecs = vecs
        return words, vecs

    def _dump_nearest(self, day: int, secret: str, k: int = 1000) -> dict[str, tuple[Any, float]]:
        words, mat = self._load_valid_nearest()
        try:
            word_idx = words.index(secret)
        except ValueError as exc:
            raise MissingDataError(f"secret word {secret!r} is not in valid_nearest.dat") from exc

        vec = mat[word_idx]
        dists = mat.dot(vec) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(vec))
        top_idxs = np.argpartition(dists, -k - 1)[-k - 1:]
        dist_sort_args = dists[top_idxs].argsort()[::-1]
        words_array = np.array(words)

        nearest: dict[str, tuple[Any, float]] = {}
        for idx, candidate_idx in enumerate(top_idxs[dist_sort_args]):
            nearest[str(words_array[candidate_idx])] = (idx, float(dists[candidate_idx]))
        nearest[secret] = (ANSWER_RANK, 1.0)

        near_dir = self.data_dir / "near"
        near_dir.mkdir(parents=True, exist_ok=True)
        with (near_dir / f"{day}.dat").open("wb") as f:
            pickle.dump(nearest, f)
        return nearest

    def _similarity(self, secret: str, word: str) -> float:
        db_path = self.data_dir / "valid_guesses.db"
        if not db_path.exists():
            raise MissingDataError(
                "data/valid_guesses.db is missing. Run ./scripts/bootstrap_data.sh first, "
                "or set KKOMA_ENGINE_MODE=remote for a quick non-self-hosted smoke test."
            )

        with sqlite3.connect(db_path) as connection:
            return cosine_similarity(
                self._word_vec(connection, secret),
                self._word_vec(connection, word),
            )

    @staticmethod
    def _word_vec(connection: sqlite3.Connection, word: str) -> np.ndarray:
        row = connection.execute("SELECT vec FROM guesses WHERE word == ?", (word,)).fetchone()
        if row is None:
            raise UnknownWordError(word)
        return pickle.loads(row[0])


class RemoteSemantleEngine:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def today(self) -> int:
        return today_puzzle()

    def answer(self, day: int | None = None) -> str:
        puzzle_day = self.today() if day is None else day
        return str(self._read_json(f"/top_scores/{puzzle_day}")["key"])

    def guess(self, word: str, day: int | None = None) -> GuessResult:
        puzzle_day = self.today() if day is None else day
        payload = self._read_json(f"/guess/{puzzle_day}/{quote(word)}")
        rank = str(payload["rank"])
        similarity = float(payload["sim"])
        is_answer = similarity >= 0.999999 or rank == ANSWER_RANK
        return GuessResult(
            day=puzzle_day,
            guess=str(payload["guess"]),
            similarity=similarity,
            rank=ANSWER_RANK if is_answer else rank,
            is_answer=is_answer,
        )

    def top_scores(self, day: int | None = None) -> list[TopScore]:
        puzzle_day = self.today() if day is None else day
        payload = self._read_json(f"/top_scores/{puzzle_day}")
        return [
            TopScore(rank=int(rank), word=str(word), similarity=float(similarity))
            for rank, word, similarity in payload["top_scores"]
        ]

    def _read_json(self, path: str) -> dict[str, Any]:
        try:
            with urlopen(f"{self.base_url}{path}", timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                raise UnknownWordError(path) from exc
            raise EngineError(f"remote engine returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise EngineError(f"remote engine unavailable: {exc.reason}") from exc

    def _read_text(self, path: str) -> str:
        try:
            with urlopen(f"{self.base_url}{path}", timeout=5) as response:
                return response.read().decode("utf-8").strip()
        except (HTTPError, URLError) as exc:
            raise EngineError(f"remote engine unavailable: {exc}") from exc


EN_FIRST_DAY = date(2024, 1, 1)
_NEARBY_CELL_RE = re.compile(r"<td>\s*([^<]*?)\s*</td>")


class EnglishSemantleEngine:
    def __init__(self, data_dir: Path, base_url: str = "https://legacy.semantle.com") -> None:
        # semantle.com serves a JS app shell for /nearby_1k; legacy.semantle.com
        # returns the server-rendered neighbor table that we parse.
        self.data_dir = data_dir
        self.base_url = base_url.rstrip("/")
        self.secrets = self._load_secrets()
        self._secret_vec_cache: dict[int, np.ndarray] = {}
        self._nearby_cache: dict[int, list[TopScore]] = {}

    def today(self) -> int:
        current_date = datetime.now(tz=KST).date()
        return (current_date - EN_FIRST_DAY).days % len(self.secrets)

    def answer(self, day: int | None = None) -> str:
        return self.secrets[(self.today() if day is None else day) % len(self.secrets)]

    def guess(self, word: str, day: int | None = None) -> GuessResult:
        puzzle_day = (self.today() if day is None else day) % len(self.secrets)
        secret = self.secrets[puzzle_day]

        is_answer = word == secret
        if is_answer:
            return GuessResult(
                day=puzzle_day, guess=word, similarity=1.0, rank=ANSWER_RANK, is_answer=True
            )

        payload = self._model2(secret, word)
        if payload is None:
            raise UnknownWordError(word)

        secret_vec = self._secret_vec(puzzle_day, secret)
        word_vec = np.array(payload["vec"], dtype=float)
        similarity = cosine_similarity(secret_vec, word_vec)

        percentile = payload.get("percentile")
        if percentile is None:
            rank = OUT_OF_RANK
        else:
            rank = f"{1000 - int(percentile)}위"

        return GuessResult(
            day=puzzle_day,
            guess=word,
            similarity=float(similarity),
            rank=rank,
            is_answer=False,
        )

    def top_scores(self, day: int | None = None) -> list[TopScore]:
        puzzle_day = (self.today() if day is None else day) % len(self.secrets)
        if puzzle_day in self._nearby_cache:
            return self._nearby_cache[puzzle_day]

        cached = self._load_near_file(puzzle_day)
        if cached is not None:
            self._nearby_cache[puzzle_day] = cached
            return cached

        secret = self.secrets[puzzle_day]
        scores = self._fetch_nearby(secret)
        self._dump_near_file(puzzle_day, scores)
        self._nearby_cache[puzzle_day] = scores
        return scores

    def _secret_vec(self, day: int, secret: str) -> np.ndarray:
        if day in self._secret_vec_cache:
            return self._secret_vec_cache[day]
        payload = self._model2(secret, secret)
        if payload is None:
            raise MissingDataError(f"secret word {secret!r} not in semantle vocab")
        vec = np.array(payload["vec"], dtype=float)
        self._secret_vec_cache[day] = vec
        return vec

    def _model2(self, secret: str, word: str) -> dict[str, Any] | None:
        body = self._read_remote(f"/model2/{quote(secret)}/{quote(word)}")
        if not body.strip():
            return None
        return json.loads(body)

    def _fetch_nearby(self, secret: str) -> list[TopScore]:
        encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
        html = self._read_remote(f"/nearby_1k/{encoded}")
        cells = _NEARBY_CELL_RE.findall(html)
        scores: list[TopScore] = []
        for i in range(0, len(cells) - 2, 3):
            proximity, candidate, similarity = cells[i], cells[i + 1], cells[i + 2]
            if not proximity.isdigit():
                continue
            if candidate == secret:
                continue
            scores.append(
                TopScore(
                    rank=1000 - int(proximity),
                    word=candidate,
                    similarity=float(similarity) / 100.0,
                )
            )
        return sorted(scores, key=lambda score: score.rank)

    def _load_secrets(self) -> list[str]:
        path = self.data_dir / "secrets.txt"
        if not path.exists():
            raise MissingDataError(f"missing {path}")
        secrets = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not secrets:
            raise MissingDataError(f"{path} is empty")
        return secrets

    def _near_path(self, day: int) -> Path:
        return self.data_dir / "near" / f"{day}.dat"

    def _load_near_file(self, day: int) -> list[TopScore] | None:
        path = self._near_path(day)
        if not path.exists():
            return None
        with path.open("rb") as f:
            return pickle.load(f)

    def _dump_near_file(self, day: int, scores: list[TopScore]) -> None:
        near_dir = self.data_dir / "near"
        near_dir.mkdir(parents=True, exist_ok=True)
        with self._near_path(day).open("wb") as f:
            pickle.dump(scores, f)

    def _read_remote(self, path: str) -> str:
        request = Request(f"{self.base_url}{path}", headers={"User-Agent": "kkoma-slack/1.0"})
        try:
            with urlopen(request, timeout=10) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code == 404:
                raise UnknownWordError(path) from exc
            raise EngineError(f"semantle.com returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise EngineError(f"semantle.com unavailable: {exc.reason}") from exc
