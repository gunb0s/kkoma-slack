from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def find_dictionary_file() -> Path:
    candidates = [
        DATA / "ko-aff-dic-0.7.92" / "ko.dic",
        DATA / "ko-aff-dic-0.7.92" / "ko-aff-dic-0.7.92" / "ko.dic",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("ko.dic was not found under data/ko-aff-dic-0.7.92")


def is_hangul(text: str) -> bool:
    return bool(re.match(r"^[\u3130-\u318F\uAC00-\uD7A3]+$", text))


source = find_dictionary_file()
target = source.parent / "ko_filtered.txt"

words = []
for line in source.read_text(encoding="utf-8").splitlines():
    word = unicodedata.normalize("NFC", line.strip().split("/")[0])
    if is_hangul(word):
        words.append(word)

target.write_text("\n".join(words), encoding="utf-8")
print(f"wrote {len(words)} words to {target}")
