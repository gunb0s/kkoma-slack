from pathlib import Path
import pickle
import re
import sqlite3
import unicodedata

import numpy as np
from numpy import array
from tqdm import tqdm


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def find_filtered_dictionary() -> Path:
    candidates = [
        DATA / "ko-aff-dic-0.7.92" / "ko_filtered.txt",
        DATA / "ko-aff-dic-0.7.92" / "ko-aff-dic-0.7.92" / "ko_filtered.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("ko_filtered.txt was not found. Run scripts/filter_words.py first.")


def is_hangul(text: str) -> bool:
    return bool(re.match(r"^[\u3130-\u318F\uAC00-\uD7A3]+$", text))


def load_dic(path: Path) -> set[str]:
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = unicodedata.normalize("NFC", line.strip())
        if is_hangul(word):
            words.add(word)
    return words


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in tqdm(f, desc="Counting lines", mininterval=1))


connection = sqlite3.connect(DATA / "valid_guesses.db")
cursor = connection.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS guesses (word text PRIMARY KEY, vec blob)")

normal_words = load_dic(find_filtered_dictionary())
valid_nearest = []
valid_nearest_mat = []
eliminated = 0
checked_words = set()
total_lines = count_lines(DATA / "cc.ko.300.vec") - 1

with (DATA / "cc.ko.300.vec").open("r", encoding="utf-8", errors="ignore") as w2v_file:
    _ = w2v_file.readline()
    progress = tqdm(total=total_lines, desc="Processing vectors", mininterval=1)
    for n, line in enumerate(w2v_file):
        parts = line.rstrip().split(" ")
        word = unicodedata.normalize("NFC", parts[0])
        if not is_hangul(word) or word in checked_words:
            eliminated += 1
        else:
            vec = array([float(value) for value in parts[1:]])
            if word in normal_words:
                valid_nearest.append(word)
                valid_nearest_mat.append(vec)
            cursor.execute("INSERT OR REPLACE INTO guesses values (?, ?)", (word, pickle.dumps(vec)))
        checked_words.add(word)
        if n % 100000 == 0:
            connection.commit()
        progress.update()
    progress.refresh()

connection.commit()
connection.close()

print("invalid:", eliminated)
valid_nearest_mat = np.array(valid_nearest_mat)
print("valid nearest shape:", valid_nearest_mat.shape)

with (DATA / "valid_nearest.dat").open("wb") as f:
    pickle.dump((valid_nearest, valid_nearest_mat), f)
print("done pickling matrix")
