#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p data/near

if [ ! -f data/cc.ko.300.vec ]; then
  if [ ! -f data/cc.ko.300.vec.gz ]; then
    curl -L https://dl.fbaipublicfiles.com/fasttext/vectors-crawl/cc.ko.300.vec.gz -o data/cc.ko.300.vec.gz
  fi
  gzip -dk data/cc.ko.300.vec.gz
fi

if [ ! -d data/ko-aff-dic-0.7.92 ]; then
  if [ ! -f data/ko-aff-dic-0.7.92.zip ]; then
    curl -L https://github.com/spellcheck-ko/hunspell-dict-ko/releases/download/0.7.92/ko-aff-dic-0.7.92.zip -o data/ko-aff-dic-0.7.92.zip
  fi
  python -m zipfile -e data/ko-aff-dic-0.7.92.zip data/ko-aff-dic-0.7.92
fi

if [ "${KKOMA_SKIP_FILTER:-0}" = "1" ]; then
  python scripts/build_unfiltered_dictionary.py
else
  python scripts/filter_words.py
fi

python scripts/process_vecs.py
