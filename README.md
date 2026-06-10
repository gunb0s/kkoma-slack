# kkoma-slack

Slack 채널에서 팀원들과 같이 푸는 꼬맨틀 slash command 앱입니다.

원본 [NewsJelly/semantle-ko](https://github.com/NewsJelly/semantle-ko)의 날짜 규칙, `secrets.txt`, fastText 기반 유사도 계산을 사용합니다. 앱 자체는 Slack SDK 없이 Flask endpoint 하나로 동작합니다.

## 기능

- `/kkoma start`: 오늘의 꼬맨틀 시작
- `/kkoma 사과`: 단어 추측
- `/kkoma guess 사과`: 단어 추측
- `/kkoma top`: 현재 채널의 가까운 추측 TOP 20
- `/kkoma hint`: medium 힌트 공개
- `/kkoma hint weak`: 700~900위권 힌트 공개
- `/kkoma hint medium`: 300~500위권 힌트 공개
- `/kkoma hint strong`: 100~200위권 힌트 공개
- `/kkoma status`: 진행 현황
- `/kkoma giveup`: 정답 공개

## 빠른 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m kkoma_slack.app
```

기본 포트는 `3339`입니다. 기본값은 `self_hosted`라서 데이터가 준비되지 않으면 추측 시 오류를 냅니다. 앱 wiring만 먼저 확인하려면 `.env`에서 아래처럼 바꿀 수 있습니다.

```bash
KKOMA_ENGINE_MODE=remote
KKOMA_REMOTE_BASE_URL=https://semantle-ko.newsjel.ly
```

`remote` 모드는 빠른 smoke test용입니다. Slack 봇이 public 꼬맨틀 서버를 호출하므로, 실제 운영은 아래 self-hosted 구성을 권장합니다.

## self-hosted 데이터 준비

정확한 꼬맨틀 방식으로 운영하려면 fastText 한국어 벡터와 한국어 사전으로 `valid_guesses.db`, `valid_nearest.dat`를 만들어야 합니다.

```bash
source .venv/bin/activate
pip install -r requirements-data.txt
./scripts/bootstrap_data.sh
```

이 작업은 `cc.ko.300.vec.gz`를 내려받고 풀기 때문에 디스크와 시간이 꽤 필요합니다. 원본처럼 `smilegate-ai/kor_unsmile` 필터를 거쳐 사전 데이터를 만들며, 최초 1회만 수행하면 됩니다.

빠르게 개발용 데이터만 만들고 싶으면 아래처럼 필터링을 건너뛸 수 있습니다. 이 경우 유사도 점수는 같은 fastText 기반이지만 TOP 1000 랭킹 후보 집합이 원본과 달라질 수 있습니다.

```bash
KKOMA_SKIP_FILTER=1 ./scripts/bootstrap_data.sh
```

## Slack 앱 만들기

1. Slack API에서 새 앱을 생성합니다.
2. `slack-manifest.yml`을 가져오거나 slash command를 수동으로 추가합니다.
3. slash command URL을 `https://your-domain/slack/commands`로 설정합니다.
4. Basic Information의 Signing Secret을 `.env`에 넣습니다.

```bash
SLACK_SIGNING_SECRET=...
KKOMA_ENGINE_MODE=self_hosted
KKOMA_DATA_DIR=./data
KKOMA_STATE_DB=./data/game_state.db
```

로컬에서 테스트하려면 ngrok, Cloudflare Tunnel, Tailscale Funnel 같은 도구로 `localhost:3339`를 외부 HTTPS URL에 연결한 뒤 Slack command URL에 넣으면 됩니다.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Docker 이미지에는 `secrets.txt`만 포함됩니다. `valid_guesses.db`, `valid_nearest.dat`, `near/*.dat`는 `./data` 볼륨에 준비해 두세요.

## 배포 메모

- slash command 응답은 기본적으로 채널에 공개됩니다. 조용히 테스트하려면 `KKOMA_PUBLIC_RESPONSES=0`으로 바꾸면 됩니다.
- 같은 Slack team, channel, puzzle day 단위로 추측 기록을 저장합니다.
- 정답 목록과 날짜 계산은 KST 기준 `2022-04-01`부터 시작하는 4,650개 순환 규칙을 사용합니다.
- GPLv3 프로젝트이므로 수정본을 배포할 때는 소스와 라이선스를 함께 제공해야 합니다.
