from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import random
import time
from typing import Any, Protocol

from flask import Request

from .commands import parse_command
from .semantle_engine import OUT_OF_RANK, EngineError, MissingDataError, UnknownWordError
from .storage import StateStore, StoredGuess


TOP_LIMIT = 10
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


@dataclass(frozen=True)
class Game:
    key: str
    command: str
    display_name: str
    engine: SemantleEngine
    example_word: str = "사과"


WELCOME_TEXT = "\n".join(
    [
        "👋 꼬맨틀 / semantle 사용법",
        "",
        "오늘의 숨은 단어를 '의미 유사도'로 함께 맞히는 게임이에요.",
        "• 꼬맨틀(`/kkoma`)은 한국어, semantle(`/sema`)은 영어 단어입니다.",
        "",
        "▶ 시작하기",
        "1. 먼저 `/kkoma start` 또는 `/sema start` 로 게임을 시작하세요.",
        "   (start 하기 전에는 단어를 추측할 수 없어요)",
        "2. 한 채널에서는 꼬맨틀과 semantle을 '동시에' 진행할 수 없어요.",
        "   하나를 맞히거나 `giveup` 한 뒤에 다른 게임을 시작할 수 있습니다.",
        "",
        "▶ 플레이",
        "• `/kkoma 사과` 또는 `/sema apple` 처럼 단어를 던지면 유사도가 나와요.",
        "• `top` 랭킹 · `hint [weak|medium|strong]` 힌트 · `status` 현황 · `giveup` 정답 공개",
    ]
)


def help_text(game: Game) -> str:
    cmd = game.command
    name = game.display_name
    word = game.example_word
    return "\n".join(
        [
            f"{name} Slack 사용법",
            f"`/{cmd} start` 오늘 문제 시작",
            f"`/{cmd} {word}` 또는 `/{cmd} guess {word}` 추측",
            f"`/{cmd} top` 현재 랭킹",
            f"`/{cmd} hint [weak|medium|strong]` 힌트 공개",
            f"`/{cmd} status` 진행 현황",
            f"`/{cmd} giveup` 정답 공개",
        ]
    )


def ensure_signing_configured(signing_secret: str, allow_unsigned: bool) -> None:
    if not signing_secret and not allow_unsigned:
        raise RuntimeError(
            "SLACK_SIGNING_SECRET is not set. "
            "Set it, or set KKOMA_ALLOW_UNSIGNED=1 for local development only."
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
    games: dict[str, Game],
    store: StateStore,
    public_responses: bool = True,
) -> dict[str, Any]:
    command = (form.get("command") or "").lstrip("/")
    game = games.get(command)
    if game is None:
        game = next(iter(games.values()))

    engine = game.engine
    parsed = parse_command(form.get("text", ""))
    team_id = form.get("team_id") or form.get("enterprise_id") or "unknown-team"
    channel_id = form.get("channel_id") or "unknown-channel"
    user_id = form.get("user_id") or "unknown-user"
    user_name = form.get("user_name") or ""
    day = engine.today()

    try:
        if parsed.action == "help":
            return ephemeral(help_text(game))
        if parsed.action == "welcome":
            return ephemeral(WELCOME_TEXT)
        if parsed.action == "start":
            conflict = active_conflict(game, games, store, team_id, channel_id)
            if conflict is not None:
                other, other_day = conflict
                return ephemeral(
                    f"이 채널은 지금 {other.display_name}(#{other_day}) 진행 중이에요. "
                    f"먼저 정답을 맞히거나 `/{other.command} giveup` 후 시작할 수 있어요."
                )
            store.ensure_game(game.key, team_id, channel_id, day, user_id)
            return visible(
                f"{game.display_name} #{day} 시작! 이 채널에서 같이 오늘의 단어를 맞혀보세요.\n"
                f"`/{game.command} {game.example_word}`처럼 바로 단어를 던지면 됩니다.",
                public_responses,
            )

        if not store.has_game(game.key, team_id, channel_id, day):
            return ephemeral(f"먼저 `/{game.command} start` 해주세요.")

        if parsed.action == "top":
            total = store.guess_count(game.key, team_id, channel_id, day)
            guesses = store.guesses(game.key, team_id, channel_id, day, limit=TOP_LIMIT)
            return visible(format_top(game, guesses, day, total, rank_cutoff(engine, day)), public_responses)
        if parsed.action == "status":
            total = store.guess_count(game.key, team_id, channel_id, day)
            guesses = store.guesses(game.key, team_id, channel_id, day, limit=TOP_LIMIT)
            return visible(format_status(game, guesses, day, total, rank_cutoff(engine, day)), public_responses)
        if parsed.action == "hint":
            return handle_hint(game, team_id, channel_id, user_id, day, parsed.word, store, public_responses)
        if parsed.action == "giveup":
            store.reveal_answer(game.key, team_id, channel_id, day)
            return visible(f"{game.display_name} #{day} 정답은 `{engine.answer(day)}` 입니다.", public_responses)
        if parsed.action == "guess":
            if not parsed.word:
                return ephemeral(f"추측할 단어를 같이 입력해주세요. 예: `/{game.command} {game.example_word}`")
            return handle_guess(
                game, team_id, channel_id, user_id, user_name, day, parsed.word, store, public_responses
            )
    except MissingDataError as exc:
        return ephemeral(f"엔진 데이터가 아직 준비되지 않았습니다.\n```{exc}```")
    except UnknownWordError:
        return ephemeral(f"`{parsed.word}`는 {game.display_name} 사전에 없는 단어입니다.")
    except EngineError as exc:
        return ephemeral(f"{game.display_name} 엔진 오류가 발생했습니다.\n```{exc}```")

    return ephemeral(help_text(game))


def active_conflict(
    game: Game,
    games: dict[str, Game],
    store: StateStore,
    team_id: str,
    channel_id: str,
) -> tuple[Game, int] | None:
    for other in games.values():
        if other.key == game.key:
            continue
        other_day = other.engine.today()
        if store.is_active(other.key, team_id, channel_id, other_day):
            return other, other_day
    return None


def handle_guess(
    game: Game,
    team_id: str,
    channel_id: str,
    user_id: str,
    user_name: str,
    day: int,
    word: str,
    store: StateStore,
    public_responses: bool,
) -> dict[str, Any]:
    engine = game.engine
    duplicate = store.duplicate_guess(game.key, team_id, channel_id, day, word)
    result = engine.guess(word, day)
    inserted = store.record_guess(
        game.key,
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
    count = store.guess_count(game.key, team_id, channel_id, day)
    top_guesses = store.guesses(game.key, team_id, channel_id, day, limit=TOP_LIMIT)

    if result.is_answer:
        text = format_solved(game, user_id, result.guess, count)
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
                f"{format_top(game, top_guesses, day, count, cutoff)}"
            )

    return visible(text, public_responses)


def format_solved(game: Game, user_id: str, word: str, count: int) -> str:
    return (
        f":tada::tada: *정답입니다!* :tada::tada:\n"
        f"<@{user_id}>님이 마침내 `{word}`를 찾아냈어요! :clap:\n"
        f"무려 {count}번의 도전 끝에 오늘의 {game.display_name}을 정복했습니다. :trophy: 축하해요! :partying_face:"
    )


def handle_hint(
    game: Game,
    team_id: str,
    channel_id: str,
    user_id: str,
    day: int,
    requested_level: str,
    store: StateStore,
    public_responses: bool,
) -> dict[str, Any]:
    level = normalize_hint_level(requested_level)
    if level is None:
        return ephemeral(
            f"힌트는 `weak`, `medium`, `strong` 중 하나로 요청해주세요. 예: `/{game.command} hint medium`"
        )

    stored = store.hint(game.key, team_id, channel_id, day, level)
    if stored is None:
        candidates = [score for score in game.engine.top_scores(day) if score.rank in HINT_RANGES[level]]
        if not candidates:
            return ephemeral(f"`{level}` 힌트를 가져올 수 없습니다. 잠시 뒤 다시 시도해주세요.")
        selected = random.choice(candidates)
        stored = store.record_hint(
            game.key,
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
    game: Game,
    guesses: list[StoredGuess],
    day: int,
    total_count: int | None = None,
    cutoff: float | None = None,
) -> str:
    if not guesses:
        return f"{game.display_name} #{day}에는 아직 추측이 없습니다. `/{game.command} start`로 시작해보세요."
    total = total_count if total_count is not None else len(guesses)
    rows = [f"{game.display_name} #{day} TOP {min(len(guesses), TOP_LIMIT)} · 총 {total}개 추측"]
    rows.extend(format_top_rows(guesses, cutoff))
    return "\n".join(rows)


def format_status(
    game: Game,
    guesses: list[StoredGuess],
    day: int,
    total_count: int | None = None,
    cutoff: float | None = None,
) -> str:
    if not guesses:
        return f"{game.display_name} #{day} 진행 전입니다. `/{game.command} start`로 시작할 수 있어요."
    solved = next((guess for guess in guesses if guess.is_answer), None)
    prefix = (
        f"{game.display_name} #{day}은 이미 `{solved.word}`로 해결됐습니다."
        if solved
        else f"{game.display_name} #{day} 진행 중"
    )
    return f"{prefix}\n{format_top(game, guesses, day, total_count, cutoff)}"


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
