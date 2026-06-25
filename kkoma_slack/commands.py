from dataclasses import dataclass
import unicodedata


COMMAND_ALIASES = {
    "start": "start",
    "시작": "start",
    "guess": "guess",
    "g": "guess",
    "추측": "guess",
    "top": "top",
    "rank": "top",
    "ranking": "top",
    "순위": "top",
    "status": "status",
    "stat": "status",
    "현황": "status",
    "giveup": "giveup",
    "answer": "giveup",
    "포기": "giveup",
    "정답": "giveup",
    "hint": "hint",
    "힌트": "hint",
    "help": "help",
    "도움말": "help",
    "welcome": "welcome",
    "웰컴": "welcome",
    "안내": "welcome",
}


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    word: str = ""


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.strip())


def parse_command(text: str) -> ParsedCommand:
    normalized = normalize_word(text)
    if not normalized:
        return ParsedCommand("help")

    parts = normalized.split(maxsplit=1)
    head = parts[0].lower()
    action = COMMAND_ALIASES.get(head)

    if action is None:
        return ParsedCommand("guess", normalized)

    if action == "guess":
        return ParsedCommand("guess", normalize_word(parts[1]) if len(parts) > 1 else "")

    if action == "hint":
        return ParsedCommand("hint", parts[1].lower().strip() if len(parts) > 1 else "medium")

    return ParsedCommand(action)
