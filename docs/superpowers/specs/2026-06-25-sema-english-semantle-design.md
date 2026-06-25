# Design: `/sema` — 영어 Semantle Slack 커맨드

작성일: 2026-06-25

## 목표

기존 kkoma(한국어 꼬맨틀) Slack 앱과 **같은 서버/같은 레포**에 영어 Semantle 게임을 `/sema` 슬래시 커맨드로 추가한다. kkoma 커맨드와 **완전히 동일한 기능**(start / guess / top / status / hint / giveup / help)을 제공한다.

## 핵심 결정 사항

| 항목 | 결정 |
|------|------|
| 데이터 소스 | 외부 API 의존. `semantle.com`의 `/model2`, `/nearby_1k` 사용 |
| 정답 리스트 | 오픈소스 semantle(`lawley/semantle` 등)의 day-indexed 영어 정답 리스트를 `data/en/secrets.txt`로 번들 |
| 후보군(1000) | 번들 안 함. 런타임에 `/nearby_1k/{secret}`로 받아 캐싱 (kkoma의 `near/{day}.dat`와 동일 패턴) |
| 상태 격리 | `StateStore` 테이블에 `game` 컬럼 추가, PK에 포함 (기존 행은 `kkoma` 기본값) |
| 엔드포인트 | `/slack/commands` 하나 유지. 슬랙 form의 `command`로 게임 분기 |
| 메시지 언어 | 전부 한글 유지 (사내 한국 사용자 대상). 게임 표시명만 파라미터화 |
| 타임존/day | KST 기준 자체 day 계산. semantle.com은 secret을 인자로 받으므로 "오늘" 개념이 없어 타임존 자유 |
| 커맨드 이름 | `/sema` 확정 |

## 아키텍처

### 게임 레지스트리

`command` 문자열 → 게임 설정을 매핑하는 레지스트리를 둔다. 각 게임 설정은:

- `key`: 상태 네임스페이스 (`"kkoma"` / `"sema"`)
- `engine`: SemantleEngine 구현체
- `display_name`: 메시지에 박을 게임명 (`"꼬맨틀"` / `"semantle"`)

`handle_slash_command`가 `form["command"]`(앞의 `/` 제거)로 레지스트리에서 게임을 골라 해당 engine + state 네임스페이스로 처리한다. 엔드포인트는 그대로 하나.

### EnglishSemantleEngine (신규)

`SemantleEngine` Protocol(`today / answer / guess / top_scores`)을 구현. 외부는 `semantle.com`.

- **번들 데이터**: `data/en/secrets.txt` (day-indexed 영어 정답). `EN_NUM_SECRETS = len(secrets)`, `EN_FIRST_DAY`는 우리가 정한 시작일.
- **`today()`**: `(now(KST).date() - EN_FIRST_DAY).days % EN_NUM_SECRETS`
- **`answer(day)`**: `secrets[day]`
- **`guess(word, day)`**:
  - `GET /model2/{secret}/{quote(word)}` → `{vec:[300 float], percentile?}`
  - 빈 응답이면 `UnknownWordError`
  - secret 벡터는 `GET /model2/{secret}/{secret}`로 1회 받아 day별 캐싱
  - 유사도 = `cosine(secret_vec, word_vec)` (numpy)
  - rank: `percentile` 있으면 그것으로 "N위" 도출(가까울수록 높은 percentile), 없으면 `"1000위 이상"`
  - `is_answer = word == secret` → rank `"정답!"`, similarity 1.0
- **`top_scores(day)`**:
  - `GET /nearby_1k/{base64(secret)}` → HTML 파싱 (neighbor, percentile, similarity)
  - `TopScore(rank, word, similarity)` 리스트 반환, day별 캐싱 (메모리 + 선택적 `data/en/near/{day}.dat`)
- 에러는 기존 `EngineError / MissingDataError / UnknownWordError` 재사용.

> 공유 sentinel: `"정답!"`, `"1000위 이상"`은 엔진 공통 상수로 두고 두 엔진이 같이 쓴다.

### StateStore 변경

`games / guesses / hints` 3개 테이블에 `game TEXT NOT NULL DEFAULT 'kkoma'` 추가, PK 맨 앞에 `game` 포함. 모든 store 메서드 시그니처 맨 앞에 `game` 인자 추가(인스턴스 분리 대신 인자 전달).

마이그레이션 (**기존 데이터 무손실 — 최우선 제약**): SQLite는 PK 변경 ALTER가 안 되므로 `_init_schema`에서 신규 스키마 테이블 생성 → 기존 행을 `game='kkoma'`로 복사 → 기존 테이블 drop → rename. 신규 PK는 `(game, team_id, channel_id, day[, word/level])`. 안전장치:

- `game` 컬럼이 **없을 때만** 실행 (`PRAGMA table_info`로 감지). idempotent — 재시작/재배포해도 중복 실행 안 됨.
- 전 과정 **단일 트랜잭션** (`BEGIN`~`COMMIT`). SQLite DDL은 트랜잭션 내 롤백되므로 중간 실패 시 원본 그대로 보존.
- 복사 후 행 수 검증(`SELECT COUNT(*)` 일치) 후에만 drop.

기존 kkoma 게임 이력은 전부 보존된다.

### config

- `KKOMA_SEMA_REMOTE_BASE_URL` (기본 `https://semantle.com`)
- `KKOMA_ENABLE_SEMA` (기본 on) — sema 게임 등록 여부
- kkoma 기존 설정은 그대로.

### app.py

`create_app`에서 게임 레지스트리를 구성: kkoma engine(기존 `create_engine`) + sema engine(`EnglishSemantleEngine`). `handle_slash_command`에 레지스트리/게임 전달.

## 한 채널 = 활성 게임 1개 (동시진행 차단)

같은 채널에서 kkoma와 sema가 동시에 진행되지 않도록 한다.

- **start가 유일한 시작점**: 게임은 `start` 커맨드로만 시작된다. `guess`가 더 이상 게임을 자동 생성하지 않는다(`handle_guess`의 암묵적 `ensure_game` 제거). start 안 된 채널에서 `guess`/`hint`/`top`/`status`/`giveup` 하면 `먼저 /{cmd} start 해주세요` 안내. **kkoma에도 동일 적용**(기존 자동시작 UX 변경 — 의도된 변경).
- **start 시 락 검사**: 게임 X를 start할 때, 같은 `(team, channel)`에서 다른 게임 Y가 **활성**이면 거부 + 안내(`이 채널은 지금 {Y display_name}(#day) 진행 중이에요. 먼저 정답을 맞히거나 /{Y} giveup 후 시작할 수 있어요.`). 누가 먼저 시작하는지는 무관.
- **활성 정의**: 게임 Y의 `day == engine_Y.today()` 행이 존재하고 `solved_by IS NULL AND answer_revealed == 0`. kkoma/sema는 day 카운터가 다르므로 "같은 day"가 아니라 "상대 게임의 *오늘* 퍼즐이 미완으로 떠 있는가"로 판단.
- **해제**: 현재 게임을 풀거나(`solved`) `giveup` 하면 활성 아님 → 같은 날 다른 게임 start 허용(동시만 막고 순차는 허용). 날이 바뀌면 어제 게임은 today가 아니라 자동으로 활성 해제.
- **구현 위치**: 락 검사 헬퍼를 `slack_app.py`에 두고 `start` 액션에서만 호출. StateStore에 "채널의 특정 game이 day에 활성인가" 조회 메서드 추가(`solved_by IS NULL AND answer_revealed=0` 확인).

## 메시지

기존 한글 메시지를 재사용하되 `"꼬맨틀"` 하드코딩을 게임 `display_name`으로 치환. 영어 단어는 한글 문장 안에 그대로 삽입 (예: `semantle #123 시작!`).

## 배포 / Slack 설정

- 인프라 변경 없음 (같은 컨테이너/서버).
- Docker 이미지에 `data/en/secrets.txt` 포함.
- **새 Slack 앱 만들지 않음.** 기존 kkoma Slack 앱에 슬래시 커맨드 `/sema`만 추가 (URL 동일 `/slack/commands`, 같은 Signing Secret 사용). `slack-manifest.yml`의 `slash_commands` 리스트에 항목 추가.
- **배포 전 운영 DB 백업**: deploy 단계(EC2 `/opt/kkoma/deploy.sh`)에서 컨테이너 교체 전 `game_state.db`를 타임스탬프 백업으로 `cp`. 마이그레이션 롤백 안전장치와 별개의 2차 방어선.

## 테스트

- 커맨드 파싱: 기존 공유 (`parse_command`) — 변경 없음, 회귀 확인.
- `EnglishSemantleEngine`: HTTP 모킹으로 guess/top_scores/unknown-word 검증.
- `StateStore`: game 네임스페이스 격리 (kkoma/sema 같은 channel·day 충돌 없음) 검증.
- dispatch 라우팅: `command`별로 올바른 engine/네임스페이스 선택 검증.

## 리스크 / 검증거리

- **(구현 착수 시 스모크 테스트 필수)** 라이브 `semantle.com`이 `/model2/{secret}/{word}`·`/nearby_1k/{b64}`를 인증 없이 위 모양대로 응답하는지 확인. 다르면 base_url을 호환 인스턴스로 교체하거나 어댑터 보정.
- 외부 API 가용성/레이트리밋은 외부 의존의 본질적 리스크로 수용. 후보군 캐싱으로 호출 최소화.
- 영어 정답 단어가 semantle.com word2vec 사전에 존재해야 `/model2`가 벡터 반환 — 공식 정답 리스트 사용으로 보장.

## 범위 밖 (YAGNI)

- 영어 메시지 i18n 카탈로그 (한글 유지).
- 후보군 사전 번들/self-host.
- kkoma 기존 동작 변경.
