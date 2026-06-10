# kkomantle Slack 앱 AWS EC2 + Terraform 배포 설계

날짜: 2026-06-10

## 목표

로컬에서 돌리던 kkoma-slack(Slack slash command 앱)을 AWS EC2 위에 올려 상시 운영한다.
인프라는 Terraform으로 관리하고, 배포는 GitHub Actions → ECR → EC2 순으로 자동화한다.
최대한 단순하게 구성한다.

## 확정된 결정 사항

| 항목 | 결정 |
|---|---|
| HTTPS | API Gateway HTTP API 기본 도메인 (도메인 비용 없음, 요청량 기반 과금은 발생 — 이 트래픽 규모에서는 미미) |
| 엔진 모드 | `remote` 유지 (public 꼬맨틀 서버 호출, self-hosted 데이터 준비 안 함) |
| 인스턴스 | t4g.nano (arm64, 0.5GB RAM) + 1GB 스왑 파일. 메모리 여유가 얇아 운영 중 부족 징후 시 t4g.micro 상향 (terraform 변수 한 줄) |
| 디스크 | 루트 EBS 8GB gp3 (AL2023 최소 크기) |
| 리전 | ap-northeast-2 (서울) |
| 시크릿 | SSM Parameter Store SecureString (무료 티어) |
| GitHub 계정 | `gunb0s` |
| 저장소 구성 | 2개 분리: 앱 저장소(`kkoma-slack`, public) + 인프라 저장소(`~/aws-infra`, AWS 계정 총괄용 신규) |
| 배포 자동화 | GitHub Actions → ECR push → SSM send-command로 EC2 재배포 |
| Actions → AWS 인증 | OIDC role (액세스 키 미사용) |
| Terraform 상태 | local state (인프라 저장소에 커밋하지 않음) |
| 예상 비용 | 최소 지속 비용 월 약 $7.5 (t4g.nano $3.1 + 퍼블릭 IPv4 $3.6 + EBS $0.7). API Gateway 요청, ECR 스토리지, 데이터 전송 등 사용량 기반 소액이 추가될 수 있음 |

## 런타임 아키텍처

```
Slack slash command
   ↓ HTTPS
API Gateway HTTP API (https://xxx.execute-api.ap-northeast-2.amazonaws.com)
   ↓ HTTP proxy: ANY /slack/commands → http://<EIP 퍼블릭 DNS>:3339/slack/commands
EC2 t4g.nano (Amazon Linux 2023, arm64) + EIP
   └─ Docker 컨테이너 (gunicorn, --restart always)
       └─ 호스트 볼륨 /opt/kkoma/data → SQLite game_state.db
```

- EC2 부팅 시 user_data가: docker 설치 → 1GB 스왑 생성 → SSM에서 시크릿 읽어 `.env` 작성 → 배포 스크립트(`/opt/kkoma/deploy.sh`) 설치 후 실행
- `deploy.sh`: ECR 로그인 → `:latest` pull → 컨테이너 교체 기동. 부팅 시와 Actions의 SSM send-command가 같은 스크립트를 사용한다.
- SQLite는 호스트 디렉토리에 있어 컨테이너 교체에도 유지된다. 인스턴스 자체를 재생성하면 게임 기록은 사라진다 (허용).
- 보안: SG는 3339 인바운드만 개방 (API Gateway는 고정 IP가 없어 소스 제한 불가). 비인가 요청은 앱의 Slack 서명 검증이 거부한다. SSH 포트는 열지 않고 SSM Session Manager로 접속한다.
- **서명 검증 fail-closed (앱 코드 변경 포함)**: 현재 `verify_slack_request()`는 `SLACK_SIGNING_SECRET`이 비어 있으면 검증 없이 통과시킨다(fail-open). 운영에서 SSM 읽기나 `.env` 작성이 실패하면 공개 엔드포인트가 무방비가 되므로, 시크릿이 없으면 앱이 시작하지 않도록 변경한다. 로컬 개발용 우회는 `KKOMA_ALLOW_UNSIGNED=1` 명시 시에만 허용. user_data도 SSM 파라미터 읽기 실패 시 컨테이너를 기동하지 않고 종료한다(fallback 없음).
- EC2 인스턴스 롤: ECR pull + SSM(에이전트 + 파라미터 읽기)

## 배포 파이프라인

1. 앱 저장소 `main` push 시 GitHub Actions가:
   - `ubuntu-24.04-arm` 러너(public repo 무료)에서 arm64 네이티브 빌드
   - ECR push (`:latest` + `:<sha>`)
   - `aws ssm send-command`로 EC2에 docker pull + 컨테이너 재시작 지시
2. Actions의 AWS 인증은 Terraform이 만든 OIDC role을 assume (저장된 키 없음)
3. 이후 업데이트는 `git push`만으로 자동 배포

## Terraform 인프라 저장소 (`~/aws-infra`)

AWS 계정 전체를 총괄하는 저장소로 키울 전제. 스택(디렉토리)별로 state를 분리해
이 프로젝트를 첫 스택으로 시작한다.

```
~/aws-infra/
├── global/
│   └── github-oidc/   # GitHub OIDC provider (계정당 1개 — 공용 스택으로 분리)
└── kkoma-slack/       # 이 프로젝트 스택
```

`kkoma-slack` 스택 리소스:
- ECR 리포지토리
- EC2 인스턴스 + EIP + Security Group + IAM 인스턴스 롤
- user_data 스크립트
- API Gateway HTTP API + HTTP 프록시 통합
- Actions deploy role (신뢰 조건: `gunb0s/kkoma-slack`, global 스택의 OIDC provider 참조)
- SSM Parameter (`SLACK_SIGNING_SECRET` — 값은 apply 전 CLI로 주입, terraform은 참조만)

default VPC를 사용한다. 스택 내부는 모듈 분리 없이 평탄한 구성으로 작성한다.

## 첫 배포 순서

1. `gh auth switch -u gunb0s` + `gh auth refresh -s workflow` (workflow scope 추가)
2. `~/aws-infra` 저장소 생성, `global/github-oidc` 스택 apply
3. 시크릿을 SSM에 주입 (`aws ssm put-parameter`)
4. `kkoma-slack` 스택 apply → API Gateway URL 출력
   - 이 시점에는 ECR에 이미지가 없어 컨테이너는 뜨지 않는다. `deploy.sh`는 pull 실패 시 정상 종료하고, 인스턴스/배포 경로만 준비된 상태가 된다.
5. 앱 저장소 생성 + push → Actions가 첫 이미지 빌드 → SSM으로 `deploy.sh` 실행 → 이때 서비스가 처음 기동된다
6. Slack 앱 설정에서 command URL을 API Gateway URL로 교체

## 에러 처리 / 운영

- Slack 3초 타임아웃: remote 모드의 외부 호출 지연은 현 로컬 운영과 동일 조건이라 그대로 둔다.
- 컨테이너 비정상 종료: `--restart always`로 자동 복구.
- 인스턴스 장애: 수동 `terraform apply`로 재생성 (게임 기록 유실 허용).
- 메모리 부족 징후(OOM kill, 과도한 스왑) 발견 시 인스턴스 타입 변수만 t4g.micro로 올려 재생성.
- 로그 확인: SSM Session Manager 접속 후 `docker logs`.

## 테스트

- 앱: 시크릿 미설정 시 시작 실패, `KKOMA_ALLOW_UNSIGNED=1`일 때만 우회 — 단위 테스트 추가 (기존 pytest 스위트에)
- Terraform: `terraform validate` + `terraform plan` 검토
- 배포 후: `curl`로 API Gateway 엔드포인트 응답 확인 (서명 없는 요청 → 401/403 기대), Slack에서 `/kkoma status` 실제 호출 확인
- Actions: 더미 커밋 push로 빌드→ECR→재배포 사이클 검증

## 추후 확장 (이번 범위 아님)

- self-hosted 엔진 전환: 데이터 파일을 S3에 올리고 user_data에서 받도록 확장. 인스턴스 사양 상향 필요할 수 있음.
- 도메인 구입 시: API Gateway 커스텀 도메인 + ACM으로 교체.
