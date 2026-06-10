from pathlib import Path
import unicodedata

import tqdm
from transformers import AutoTokenizer, BertForSequenceClassification, TextClassificationPipeline


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


def clean_label(output_labels, min_score):
    for label in output_labels:
        if label["score"] > min_score:
            return label
    return {"label": "unknown", "score": 0}


def filter_lines(words, pipe):
    filtered = []
    for index, output in enumerate(tqdm.tqdm(pipe(x for x in words), total=len(words))):
        label = clean_label(output, 0.5)
        if label["label"] == "clean":
            filtered.append(words[index])
        else:
            print(f'filtered: {words[index]} - {label["label"]}/{label["score"]}')
    return filtered


model_name = "smilegate-ai/kor_unsmile"
model = BertForSequenceClassification.from_pretrained(model_name)
tokenizer = AutoTokenizer.from_pretrained(model_name)
pipe = TextClassificationPipeline(
    model=model,
    tokenizer=tokenizer,
    device=-1,
    return_all_scores=True,
    function_to_apply="sigmoid",
)

frequent_words = [
    unicodedata.normalize("NFC", line.strip())
    for line in (DATA / "frequent_words.txt").read_text(encoding="utf-8").splitlines()
]
filtered_frequent_path = DATA / "filtered_frequent_words.txt"
if filtered_frequent_path.exists():
    print(f"using existing {filtered_frequent_path}")
else:
    filtered_frequent_path.write_text("\n".join(filter_lines(frequent_words, pipe)), encoding="utf-8")

dictionary_path = find_dictionary_file()
dictionary_words = [
    unicodedata.normalize("NFC", line.strip().split("/")[0])
    for line in dictionary_path.read_text(encoding="utf-8").splitlines()
]
filtered_dictionary_path = dictionary_path.parent / "ko_filtered.txt"
filtered_dictionary_path.write_text(
    "\n".join(filter_lines(dictionary_words, pipe)),
    encoding="utf-8",
)
print(f"wrote filtered dictionary to {filtered_dictionary_path}")
