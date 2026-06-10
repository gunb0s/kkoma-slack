from __future__ import annotations

import hashlib
import hmac
import random
import time
from typing import Any, Protocol

from flask import Request

from .commands import parse_command
from .semantle_engine import EngineError, MissingDataError, UnknownWordError
from .storage import StateStore, StoredGuess


TOP_LIMIT = 20
OUT_OF_RANK = "1000위 이상"
NEAR_BUT_UNRANKED = "🔥 후보엔 없지만 가까워요"
_cutoff_cache: dict[int, float] = {}
HINT_RANGES = {
    "weak": range(700, 901),
    "medium": range(300, 501),
    "strong": range(100, 201),
}
HINT_ALIASES = {
    "약": "weak",
    "약함": "weak",
    "쉬움": "weak",
    "중": "medium",
    "중간": "medium",
    "보통": "medium",
    "강": "strong",
    "강함": "strong",
}


class SemantleEngine(Protocol):
    def today(self) -> int: ...
    def answer(self, day: int | None = None) -> str: ...
    def guess(self, word: str, day: int | None = None): ...
    def top_scores(self, day: int | None = None): ...


HELP_TEXT = "\n".join(
    [
        "꼬맨틀 Slack 사용법",
        "`/kkoma start` 오늘 문제 시작",
        "`/kkoma 사과` 또는 `/kkoma guess 사과` 추측",
        "`/kkoma top` 현재 랭킹",
        "`/kkoma hint [weak|medium|strong]` 힌트 공개",
        "`/kkoma status` 진행 현황",
        "`/kkoma giveup` 정답 공개",
    ]
)


def verify_slack_request(request: Request, signing_secret: str) -> bool:
    if not signing_secret:
        return True

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not timestamp or not signature:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - request_ts) > 60 * 5:
        return False

    body = request.get_data(cache=True).decode("utf-8")
    base = f"v0:{timestamp}:{body}"
    digest = hmac.new(signing_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def handle_slash_command(
    form: dict[str, str],
    engine: SemantleEngine,
    store: StateStore,
    public_responses: bool = True,
) -> dict[str, Any]:
    parsed = parse_command(form.get("text", ""))
    team_id = form.get("team_id") or form.get("enterprise_id") or "unknown-team"
    channel_id = form.get("channel_id") or "unknown-channel"
    user_id = form.get("user_id") or "unknown-user"
    user_name = form.get("user_name") or ""
    day = engine.today()

    try:
        if parsed.action == "help":
            return ephemeral(HELP_TEXT)
        if parsed.action == "start":
            store.ensure_game(team_id, channel_id, day, user_id)
            return visible(
                f"꼬맨틀 #{day} 시작! 이 채널에서 같이 오늘의 단어를 맞혀보세요.\n"
                "`/kkoma 사과`처럼 바로 단어를 던지면 됩니다.",
                public_responses,
            )
        if parsed.action == "top":
            total = store.guess_count(team_id, channel_id, day)
            guesses = store.guesses(team_id, channel_id, day, limit=TOP_LIMIT)
            return visible(format_top(guesses, day, total, rank_cutoff(engine, day)), public_responses)
        if parsed.action == "status":
            total = store.guess_count(team_id, channel_id, day)
            guesses = store.guesses(team_id, channel_id, day, limit=TOP_LIMIT)
            return visible(format_status(guesses, day, total, rank_cutoff(engine, day)), public_responses)
        if parsed.action == "hint":
            return handle_hint(team_id, channel_id, user_id, day, parsed.word, engine, store, public_responses)
        if parsed.action == "giveup":
            store.ensure_game(team_id, channel_id, day, user_id)
            store.reveal_answer(team_id, channel_id, day)
            return visible(f"꼬맨틀 #{day} 정답은 `{engine.answer(day)}` 입니다.", public_responses)
        if parsed.action == "guess":
            if not parsed.word:
                return ephemeral("추측할 단어를 같이 입력해주세요. 예: `/kkoma 사과`")
            return handle_guess(
                team_id, channel_id, user_id, user_name, day, parsed.word, engine, store, public_responses
            )
    except MissingDataError as exc:
        return ephemeral(f"엔진 데이터가 아직 준비되지 않았습니다.\n```{exc}```")
    except UnknownWordError:
        return ephemeral(f"`{parsed.word}`는 꼬맨틀 사전에 없는 단어입니다.")
    except EngineError as exc:
        return ephemeral(f"꼬맨틀 엔진 오류가 발생했습니다.\n```{exc}```")

    return ephemeral(HELP_TEXT)


def handle_guess(
    team_id: str,
    channel_id: str,
    user_id: str,
    user_name: str,
    day: int,
    word: str,
    engine: SemantleEngine,
    store: StateStore,
    public_responses: bool,
) -> dict[str, Any]:
    store.ensure_game(team_id, channel_id, day, user_id)
    duplicate = store.duplicate_guess(team_id, channel_id, day, word)
    result = engine.guess(word, day)
    inserted = store.record_guess(
        team_id,
        channel_id,
        day,
        user_id,
        user_name,
        result.guess,
        result.similarity,
        result.rank,
        result.is_answer,
    )
    count = store.guess_count(team_id, channel_id, day)
    top_guesses = store.guesses(team_id, channel_id, day, limit=TOP_LIMIT)

    if result.is_answer:
        text = format_solved(user_id, result.guess, count)
    else:
        cutoff = rank_cutoff(engine, day)
        if not inserted and duplicate is not None:
            text = (
                f"`{word}`는 이미 *{display_name(duplicate)}*님이 추측했어요. "
                f"유사도 {format_similarity(duplicate.similarity)}, "
                f"{format_rank(duplicate.rank, duplicate.similarity, cutoff)}"
            )
        else:
            text = (
                f"*{display_name_for(user_name, user_id)}* `{result.guess}` → "
                f"유사도 {format_similarity(result.similarity)}, "
                f"{format_rank(result.rank, result.similarity, cutoff)}\n"
                f"{format_top(top_guesses, day, count, cutoff)}"
            )

    return visible(text, public_responses)


def format_solved(user_id: str, word: str, count: int) -> str:
    return (
        f":tada::tada: *정답입니다!* :tada::tada:\n"
        f"<@{user_id}>님이 마침내 `{word}`를 찾아냈어요! :clap:\n"
        f"무려 {count}번의 도전 끝에 오늘의 꼬맨틀을 정복했습니다. :trophy: 축하해요! :partying_face:"
    )


def handle_hint(
    team_id: str,
    channel_id: str,
    user_id: str,
    day: int,
    requested_level: str,
    engine: SemantleEngine,
    store: StateStore,
    public_responses: bool,
) -> dict[str, Any]:
    level = normalize_hint_level(requested_level)
    if level is None:
        return ephemeral("힌트는 `weak`, `medium`, `strong` 중 하나로 요청해주세요. 예: `/kkoma hint medium`")

    store.ensure_game(team_id, channel_id, day, user_id)
    stored = store.hint(team_id, channel_id, day, level)
    if stored is None:
        candidates = [score for score in engine.top_scores(day) if score.rank in HINT_RANGES[level]]
        if not candidates:
            return ephemeral(f"`{level}` 힌트를 가져올 수 없습니다. 잠시 뒤 다시 시도해주세요.")
        selected = random.choice(candidates)
        stored = store.record_hint(
            team_id,
            channel_id,
            day,
            level,
            selected.rank,
            selected.word,
            selected.similarity,
            user_id,
        )
        return visible(format_new_hint(stored, user_id), public_responses)

    return visible(format_existing_hint(stored), public_responses)


def format_top(
    guesses: list[StoredGuess], day: int, total_count: int | None = None, cutoff: float | None = None
) -> str:
    if not guesses:
        return f"꼬맨틀 #{day}에는 아직 추측이 없습니다. `/kkoma start`로 시작해보세요."
    total = total_count if total_count is not None else len(guesses)
    rows = [f"꼬맨틀 #{day} TOP {min(len(guesses), TOP_LIMIT)} · 총 {total}개 추측"]
    rows.extend(format_top_rows(guesses, cutoff))
    return "\n".join(rows)


def format_status(
    guesses: list[StoredGuess], day: int, total_count: int | None = None, cutoff: float | None = None
) -> str:
    if not guesses:
        return f"꼬맨틀 #{day} 진행 전입니다. `/kkoma start`로 시작할 수 있어요."
    solved = next((guess for guess in guesses if guess.is_answer), None)
    prefix = f"꼬맨틀 #{day}은 이미 `{solved.word}`로 해결됐습니다." if solved else f"꼬맨틀 #{day} 진행 중"
    return f"{prefix}\n{format_top(guesses, day, total_count, cutoff)}"


def format_top_rows(guesses: list[StoredGuess], cutoff: float | None = None) -> list[str]:
    return [
        f"{idx}. `{guess.word}` {format_similarity(guess.similarity)}, "
        f"{format_rank(guess.rank, guess.similarity, cutoff)} - *{display_name(guess)}*"
        for idx, guess in enumerate(guesses, start=1)
    ]


def rank_cutoff(engine: SemantleEngine, day: int) -> float | None:
    if day in _cutoff_cache:
        return _cutoff_cache[day]
    try:
        scores = engine.top_scores(day)
    except EngineError:
        return None
    cutoff = min((score.similarity for score in scores), default=None)
    if cutoff is not None:
        _cutoff_cache[day] = cutoff
    return cutoff


def format_rank(rank: str, similarity: float, cutoff: float | None) -> str:
    if rank == OUT_OF_RANK and cutoff is not None and similarity >= cutoff:
        return NEAR_BUT_UNRANKED
    return rank


def display_name(guess: StoredGuess) -> str:
    return display_name_for(guess.user_name, guess.user_id)


def display_name_for(user_name: str, user_id: str) -> str:
    return user_name or user_id


def normalize_hint_level(level: str) -> str | None:
    normalized = level.strip().lower() or "medium"
    normalized = HINT_ALIASES.get(normalized, normalized)
    return normalized if normalized in HINT_RANGES else None


def format_new_hint(hint, user_id: str) -> str:
    return (
        f"<@{user_id}>님이 `{hint.level}` 힌트를 공개했습니다.\n"
        f"힌트: 정답과 {hint.rank}번째로 가까운 단어는 `{hint.word}`입니다. "
        f"유사도 {format_similarity(hint.similarity)}"
    )


def format_existing_hint(hint) -> str:
    return (
        f"오늘 이 채널의 `{hint.level}` 힌트는 이미 공개됐습니다.\n"
        f"힌트: 정답과 {hint.rank}번째로 가까운 단어는 `{hint.word}`입니다. "
        f"유사도 {format_similarity(hint.similarity)}"
    )


def format_similarity(similarity: float) -> str:
    return f"{similarity * 100:.2f}"


def visible(text: str, public_responses: bool) -> dict[str, Any]:
    return {"response_type": "in_channel" if public_responses else "ephemeral", "text": text}


def ephemeral(text: str) -> dict[str, Any]:
    return {"response_type": "ephemeral", "text": text}
