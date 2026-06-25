# Notices

This project adapts the public Korean Semantle implementation:

- NewsJelly/semantle-ko: https://github.com/NewsJelly/semantle-ko
- Original Korean game: https://semantle-ko.newsjel.ly/
- Upstream projects named in the original repository: David Turner's Semantle and Johannes Gätjen's Semantlich

The adapted engine behavior keeps the original date rule:

- KST timezone
- first puzzle date: 2022-04-01
- 4,650 secret words

The included license is GPLv3, matching the upstream repository.

## English Semantle (`/sema`)

- Upstream: David Turner's Semantle (https://semantle.com, https://gitlab.com/novalis_dt/semantle)
- Live API used at runtime: `legacy.semantle.com` (`/model2/{secret}/{word}`, `/nearby_1k/{base64(secret)}`)
- `data/en/secrets.txt` is the official day-indexed secret word list extracted from
  `https://legacy.semantle.com/assets/js/secretWords.js` (the `secretWords` array, in order).
  These words are guaranteed to exist in semantle.com's word2vec vocabulary.
  EN puzzle day rule: KST timezone, first day `2024-01-01`, cycling over the list length.
