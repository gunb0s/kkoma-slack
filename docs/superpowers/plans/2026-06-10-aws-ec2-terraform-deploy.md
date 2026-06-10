# kkomantle AWS EC2 + Terraform 배포 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬에서 돌리던 kkoma-slack 앱을 EC2에 올리고, GitHub Actions → ECR → EC2(SSM) 자동 배포와 Terraform 인프라 관리를 구축한다.

**Architecture:** Slack → API Gateway HTTP API(HTTPS) → EC2 t4g.nano의 Docker 컨테이너. 인프라는 `~/aws-infra` 저장소의 두 스택(`global/github-oidc`, `kkoma-slack`)으로 관리. 배포는 앱 저장소 main push 시 Actions가 arm64 이미지를 ECR에 올리고 SSM send-command로 EC2의 `deploy.sh`를 실행.

**Tech Stack:** Flask/gunicorn, Docker, Terraform(AWS provider ~> 5.0), GitHub Actions(OIDC), SSM Parameter Store/Run Command, API Gateway HTTP API

**스펙:** `docs/superpowers/specs/2026-06-10-aws-ec2-terraform-deploy-design.md`

**고정 값:**
- AWS 계정: `947197405729`, 리전: `ap-northeast-2`
- GitHub 계정: `gunb0s`, 앱 저장소: `gunb0s/kkoma-slack` (public), 인프라 저장소: `gunb0s/aws-infra` (private)
- ECR 리포지토리: `kkoma-slack` → `947197405729.dkr.ecr.ap-northeast-2.amazonaws.com/kkoma-slack`
- deploy role 이름(고정): `kkoma-slack-deploy`
- SSM 파라미터: `/kkoma-slack/slack-signing-secret`
- EC2 태그: `App=kkoma-slack` (SSM send-command 타게팅 기준)
- Terraform state 버킷: `aws-infra-tfstate-947197405729` (S3 백엔드, `use_lockfile` 네이티브 잠금, Terraform 1.10+ 필요)

---

## File Structure

**앱 저장소 (이 폴더):**
```
kkoma_slack/config.py            # 수정: allow_unsigned 설정 추가
kkoma_slack/slack_app.py         # 수정: ensure_signing_configured() 추가
kkoma_slack/app.py               # 수정: create_app()에서 fail-closed 검증 호출
tests/test_slack_app.py          # 수정: fail-closed 테스트 추가
.github/workflows/deploy.yml     # 생성: 빌드→ECR→SSM 배포 워크플로우
```

**인프라 저장소 (`~/aws-infra`, 신규):**
```
.gitignore                       # tfstate, .terraform 제외
global/tf-state/main.tf          # state용 S3 버킷 (bootstrap)
global/tf-state/backend.tf       # bootstrap apply 후 추가 (state를 S3로 migrate)
global/github-oidc/main.tf       # GitHub OIDC provider (계정당 1개)
kkoma-slack/versions.tf          # terraform/provider 버전
kkoma-slack/variables.tf         # region, instance_type 등
kkoma-slack/ecr.tf               # ECR 리포지토리 + 수명주기 정책
kkoma-slack/ec2.tf               # AMI/VPC 데이터소스, SG, 인스턴스, EIP
kkoma-slack/iam.tf               # EC2 인스턴스 롤
kkoma-slack/deploy_role.tf       # GitHub Actions deploy role
kkoma-slack/apigw.tf             # API Gateway HTTP API
kkoma-slack/outputs.tf           # API URL 등 출력
kkoma-slack/user_data.sh         # EC2 부팅 스크립트 (deploy.sh 설치 포함)
```

---

### Task 1: 서명 검증 fail-closed (앱 코드, TDD)

현재 `verify_slack_request()`는 secret이 비어 있으면 무조건 통과(fail-open)다. 운영에서 secret 없이 앱이 뜨는 일이 없도록 시작 시점에 막는다.

**Files:**
- Modify: `kkoma_slack/config.py`
- Modify: `kkoma_slack/slack_app.py`
- Modify: `kkoma_slack/app.py`
- Test: `tests/test_slack_app.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_slack_app.py` 상단 import에 `ensure_signing_configured` 추가:

```python
from kkoma_slack.slack_app import ensure_signing_configured, handle_slash_command
```

파일 끝 `if __name__ == "__main__":` 앞에 테스트 클래스 추가:

```python
class SigningConfigTest(unittest.TestCase):
    def test_missing_secret_raises(self):
        with self.assertRaises(RuntimeError):
            ensure_signing_configured("", allow_unsigned=False)

    def test_secret_set_passes(self):
        ensure_signing_configured("real-secret", allow_unsigned=False)

    def test_allow_unsigned_bypasses(self):
        ensure_signing_configured("", allow_unsigned=True)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python3 -m unittest tests.test_slack_app -v 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'ensure_signing_configured'`

- [ ] **Step 3: 최소 구현**

`kkoma_slack/slack_app.py`의 `verify_slack_request` 함수 바로 위에 추가:

```python
def ensure_signing_configured(signing_secret: str, allow_unsigned: bool) -> None:
    if not signing_secret and not allow_unsigned:
        raise RuntimeError(
            "SLACK_SIGNING_SECRET is not set. "
            "Set it, or set KKOMA_ALLOW_UNSIGNED=1 for local development only."
        )
```

`kkoma_slack/config.py`의 `Settings` 마지막 필드 뒤에 추가:

```python
    allow_unsigned: bool = os.environ.get("KKOMA_ALLOW_UNSIGNED", "0") == "1"
```

`kkoma_slack/app.py`의 `create_app()` 첫 줄에 검증 추가 (import도 갱신):

```python
from .slack_app import ensure_signing_configured, handle_slash_command, verify_slack_request
```

```python
def create_app() -> Flask:
    ensure_signing_configured(settings.slack_signing_secret, settings.allow_unsigned)
    app = Flask(__name__)
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python3 -m unittest discover tests -v 2>&1 | tail -5`
Expected: `OK` (전체 테스트 통과. 주의: 로컬 `.env`에 `SLACK_SIGNING_SECRET`이 있어 기존 테스트는 영향 없음)

- [ ] **Step 5: Commit**

```bash
git add kkoma_slack/config.py kkoma_slack/slack_app.py kkoma_slack/app.py tests/test_slack_app.py
git commit -m "feat: 서명 시크릿 없으면 앱 시작 실패 (fail-closed)"
```

---

### Task 2: GitHub Actions 배포 워크플로우

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: 워크플로우 파일 작성**

```yaml
name: deploy

on:
  push:
    branches: [main]

permissions:
  id-token: write
  contents: read

concurrency:
  group: deploy
  cancel-in-progress: false

env:
  AWS_REGION: ap-northeast-2
  ECR_REGISTRY: 947197405729.dkr.ecr.ap-northeast-2.amazonaws.com
  ECR_REPO: kkoma-slack

jobs:
  deploy:
    runs-on: ubuntu-24.04-arm
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::947197405729:role/kkoma-slack-deploy
          aws-region: ap-northeast-2

      - uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        run: |
          SHORT_SHA="${GITHUB_SHA::7}"
          docker build -t "$ECR_REGISTRY/$ECR_REPO:latest" -t "$ECR_REGISTRY/$ECR_REPO:$SHORT_SHA" .
          docker push --all-tags "$ECR_REGISTRY/$ECR_REPO"

      - name: Redeploy on EC2 via SSM
        run: |
          CMD_ID=$(aws ssm send-command \
            --document-name AWS-RunShellScript \
            --targets "Key=tag:App,Values=kkoma-slack" \
            --parameters 'commands=["/opt/kkoma/deploy.sh"]' \
            --query Command.CommandId --output text)
          echo "command id: $CMD_ID"
          for i in $(seq 1 30); do
            STATUS=$(aws ssm list-command-invocations --command-id "$CMD_ID" \
              --query 'CommandInvocations[0].Status' --output text)
            echo "status: $STATUS"
            case "$STATUS" in
              Success) exit 0 ;;
              Failed|Cancelled|TimedOut)
                aws ssm list-command-invocations --command-id "$CMD_ID" --details
                exit 1 ;;
            esac
            sleep 5
          done
          echo "timed out waiting for SSM command"
          exit 1
```

- [ ] **Step 2: YAML 문법 검증**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: ECR 빌드 푸시 후 SSM으로 EC2 재배포하는 deploy 워크플로우 추가"
```

---

### Task 3: `~/aws-infra` 저장소 + tf-state/github-oidc 스택

**Files:**
- Create: `~/aws-infra/.gitignore`
- Create: `~/aws-infra/global/tf-state/main.tf`
- Create: `~/aws-infra/global/github-oidc/main.tf`

- [ ] **Step 0: Terraform 1.10+ 업그레이드**

brew core 포뮬러는 라이선스 변경으로 1.5.7에서 동결됐다. hashicorp tap으로 교체:

```bash
brew unlink terraform 2>/dev/null; brew install hashicorp/tap/terraform
terraform version
```

Expected: `Terraform v1.1x.x` (>= 1.10)

- [ ] **Step 1: 저장소 초기화와 .gitignore**

```bash
mkdir -p ~/aws-infra/global/tf-state ~/aws-infra/global/github-oidc ~/aws-infra/kkoma-slack
cd ~/aws-infra && git init -b main
```

`~/aws-infra/.gitignore`:

```
.terraform/
*.tfstate
*.tfstate.*
*.tfplan
crash.log
override.tf
override.tf.json
*_override.tf
*_override.tf.json
```

- [ ] **Step 2: tf-state 스택 작성 (bootstrap)**

`~/aws-infra/global/tf-state/main.tf` — backend 블록은 일부러 없음 (버킷 생성 후 Task 6에서 추가하고 migrate):

```hcl
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"
}

resource "aws_s3_bucket" "tfstate" {
  bucket = "aws-infra-tfstate-947197405729"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

- [ ] **Step 3: OIDC provider 스택 작성**

`~/aws-infra/global/github-oidc/main.tf`:

```hcl
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket       = "aws-infra-tfstate-947197405729"
    key          = "global/github-oidc/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
  }
}

provider "aws" {
  region = "ap-northeast-2"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

output "provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}
```

- [ ] **Step 4: 검증**

```bash
cd ~/aws-infra/global/tf-state && terraform init -backend=false && terraform validate
cd ~/aws-infra/global/github-oidc && terraform init -backend=false && terraform validate
```

Expected: 둘 다 `Success! The configuration is valid.`

- [ ] **Step 5: Commit**

```bash
cd ~/aws-infra
git add .gitignore global/
git commit -m "feat: tf-state 버킷과 GitHub OIDC provider 스택 추가"
```

---

### Task 4: kkoma-slack Terraform 스택

**Files:**
- Create: `~/aws-infra/kkoma-slack/versions.tf`
- Create: `~/aws-infra/kkoma-slack/variables.tf`
- Create: `~/aws-infra/kkoma-slack/ecr.tf`
- Create: `~/aws-infra/kkoma-slack/iam.tf`
- Create: `~/aws-infra/kkoma-slack/ec2.tf`
- Create: `~/aws-infra/kkoma-slack/deploy_role.tf`
- Create: `~/aws-infra/kkoma-slack/apigw.tf`
- Create: `~/aws-infra/kkoma-slack/outputs.tf`
- Create: `~/aws-infra/kkoma-slack/user_data.sh`

- [ ] **Step 1: versions.tf / variables.tf**

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket       = "aws-infra-tfstate-947197405729"
    key          = "kkoma-slack/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}
```

`variables.tf`:

```hcl
variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "app_name" {
  type    = string
  default = "kkoma-slack"
}

variable "instance_type" {
  type    = string
  default = "t4g.nano" # 메모리 부족 징후 시 t4g.micro로 상향
}

variable "github_repo" {
  type    = string
  default = "gunb0s/kkoma-slack"
}

variable "ssm_param_name" {
  type    = string
  default = "/kkoma-slack/slack-signing-secret"
}

variable "app_port" {
  type    = number
  default = 3339
}
```

- [ ] **Step 2: ecr.tf**

```hcl
resource "aws_ecr_repository" "app" {
  name = var.app_name
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
```

- [ ] **Step 3: iam.tf (EC2 인스턴스 롤)**

```hcl
resource "aws_iam_role" "instance" {
  name = "${var.app_name}-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "instance" {
  name = "${var.app_name}-instance"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_param_name}"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.app_name}-instance"
  role = aws_iam_role.instance.name
}
```

- [ ] **Step 4: user_data.sh**

```bash
#!/bin/bash
set -euo pipefail

dnf install -y docker
systemctl enable --now docker

if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon -a || true

mkdir -p /opt/kkoma/data

SECRET=$(aws ssm get-parameter --name "${ssm_param_name}" --with-decryption \
  --region "${region}" --query Parameter.Value --output text)
if [ -z "$SECRET" ] || [ "$SECRET" = "None" ]; then
  echo "FATAL: signing secret not available; refusing to start app" >&2
  exit 1
fi

cat > /opt/kkoma/.env <<ENVEOF
SLACK_SIGNING_SECRET=$SECRET
PORT=${app_port}
KKOMA_ENGINE_MODE=remote
KKOMA_REMOTE_BASE_URL=https://semantle-ko.newsjel.ly
KKOMA_DATA_DIR=/app/data
KKOMA_STATE_DB=/app/data/game_state.db
KKOMA_PUBLIC_RESPONSES=1
ENVEOF
chmod 600 /opt/kkoma/.env

cat > /opt/kkoma/deploy.sh <<'DEPLOYEOF'
#!/bin/bash
set -euo pipefail
REGION="${region}"
IMAGE="${ecr_registry}/${app_name}:latest"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ecr_registry}"

if ! docker pull "$IMAGE"; then
  echo "image not available yet; skipping container start"
  exit 0
fi

docker rm -f ${app_name} 2>/dev/null || true
docker run -d --name ${app_name} --restart always \
  -p ${app_port}:${app_port} \
  --env-file /opt/kkoma/.env \
  -v /opt/kkoma/data:/app/data \
  "$IMAGE"
docker image prune -f
DEPLOYEOF
chmod +x /opt/kkoma/deploy.sh

/opt/kkoma/deploy.sh
```

주의: `templatefile()`이 `${...}`를 치환하므로, 셸 변수는 모두 템플릿 변수가 아닌 형태(위 코드 기준 `$SECRET`, `$REGION`, `$IMAGE`, `$CMD_ID` 없음)로 유지한다. heredoc 안의 `${region}` 등은 의도된 템플릿 치환이다.

- [ ] **Step 5: ec2.tf**

```hcl
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ami" "al2023_arm" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023*-arm64"]
  }
}

resource "aws_security_group" "app" {
  name   = var.app_name
  vpc_id = data.aws_vpc.default.id

  ingress {
    description = "app port (API Gateway has no fixed IPs; app verifies Slack signature)"
    from_port   = var.app_port
    to_port     = var.app_port
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023_arm.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  user_data = templatefile("${path.module}/user_data.sh", {
    region         = var.region
    app_name       = var.app_name
    app_port       = var.app_port
    ssm_param_name = var.ssm_param_name
    ecr_registry   = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  })
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Name = var.app_name
    App  = var.app_name
  }
}

resource "aws_eip" "app" {
  domain = "vpc"
  tags   = { Name = var.app_name }
}

resource "aws_eip_association" "app" {
  instance_id   = aws_instance.app.id
  allocation_id = aws_eip.app.id
}
```

- [ ] **Step 6: deploy_role.tf**

```hcl
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "deploy" {
  name = "${var.app_name}-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "${var.app_name}-deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"
        Condition = {
          StringEquals = { "ssm:resourceTag/App" = var.app_name }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}
```

- [ ] **Step 7: apigw.tf / outputs.tf**

`apigw.tf`:

```hcl
resource "aws_apigatewayv2_api" "app" {
  name          = var.app_name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "slack_commands" {
  api_id             = aws_apigatewayv2_api.app.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = "http://${aws_eip.app.public_dns}:${var.app_port}/slack/commands"
}

resource "aws_apigatewayv2_route" "slack_commands" {
  api_id    = aws_apigatewayv2_api.app.id
  route_key = "ANY /slack/commands"
  target    = "integrations/${aws_apigatewayv2_integration.slack_commands.id}"
}

resource "aws_apigatewayv2_integration" "healthz" {
  api_id             = aws_apigatewayv2_api.app.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = "http://${aws_eip.app.public_dns}:${var.app_port}/healthz"
}

resource "aws_apigatewayv2_route" "healthz" {
  api_id    = aws_apigatewayv2_api.app.id
  route_key = "GET /healthz"
  target    = "integrations/${aws_apigatewayv2_integration.healthz.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true
}
```

`outputs.tf`:

```hcl
output "api_endpoint" {
  value       = aws_apigatewayv2_api.app.api_endpoint
  description = "Slack command URL의 베이스. /slack/commands를 붙여 사용"
}

output "instance_id" {
  value = aws_instance.app.id
}

output "eip_public_dns" {
  value = aws_eip.app.public_dns
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}
```

- [ ] **Step 8: 검증**

Run: `cd ~/aws-infra/kkoma-slack && terraform init -backend=false && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 9: Commit**

```bash
cd ~/aws-infra
git add kkoma-slack/
git commit -m "feat: kkoma-slack 스택 추가 (EC2, ECR, API Gateway, deploy role)"
```

---

### Task 5: GitHub 계정 준비 (사용자 개입 필요)

- [ ] **Step 1: gunb0s 계정으로 전환**

Run: `gh auth switch -u gunb0s && gh auth status --active`
Expected: `Active account: true` (gunb0s)

- [ ] **Step 2: workflow scope 추가 — 사용자 직접 실행**

`gh auth refresh -h github.com -s workflow`는 브라우저 인증이 필요하다. 에이전트가 실행하면 one-time code가 출력되니 사용자에게 전달하고 완료를 기다리거나, 사용자가 직접 실행한다.

Run (완료 후 확인): `gh auth status 2>&1 | grep -A 3 gunb0s`
Expected: Token scopes에 `workflow` 포함

---

### Task 6: 인프라 적용 (시크릿 주입 → apply → infra repo push)

- [ ] **Step 1: 시크릿을 SSM에 주입**

로컬 `.env`의 값을 그대로 사용한다 (셸 히스토리에 값이 남지 않도록 파일에서 읽음):

```bash
cd /Users/gunbos/Documents/Codex/2026-06-01/new-chat/outputs/kkoma-slack
SECRET=$(grep '^SLACK_SIGNING_SECRET=' .env | cut -d= -f2-)
aws ssm put-parameter --profile personal --region ap-northeast-2 \
  --name /kkoma-slack/slack-signing-secret \
  --type SecureString --value "$SECRET" --overwrite
```

Expected: `{"Version": 1, ...}`

- [ ] **Step 2: tf-state 스택 bootstrap apply (local → S3 migrate)**

```bash
cd ~/aws-infra/global/tf-state
terraform init
terraform apply -auto-approve
```

Expected: `Apply complete! Resources: 3 added` (버킷, 버전닝, public access block)

이제 자기 state를 방금 만든 버킷으로 이전한다. `~/aws-infra/global/tf-state/backend.tf` 생성:

```hcl
terraform {
  backend "s3" {
    bucket       = "aws-infra-tfstate-947197405729"
    key          = "global/tf-state/terraform.tfstate"
    region       = "ap-northeast-2"
    use_lockfile = true
    profile      = "personal"
  }
}
```

```bash
terraform init -migrate-state -force-copy
rm -f terraform.tfstate terraform.tfstate.backup
cd ~/aws-infra && git add global/tf-state/backend.tf && git commit -m "feat: tf-state 스택 state를 S3 백엔드로 이전"
```

Expected: `Successfully configured the backend "s3"!`

- [ ] **Step 3: global/github-oidc apply**

Run: `cd ~/aws-infra/global/github-oidc && terraform init && terraform apply -auto-approve`
Expected: `Apply complete! Resources: 1 added` + `provider_arn` 출력

(이미 계정에 OIDC provider가 존재하면 `EntityAlreadyExists` 에러가 난다. 그 경우 `terraform import aws_iam_openid_connect_provider.github arn:aws:iam::947197405729:oidc-provider/token.actions.githubusercontent.com` 후 재-apply.)

- [ ] **Step 4: kkoma-slack 스택 plan 검토 후 apply**

```bash
cd ~/aws-infra/kkoma-slack
terraform init
terraform plan
```

plan에서 예상 리소스(~15개: ECR, SG, EC2, EIP, IAM 롤 2개+정책, API GW 등)만 생성되는지 확인 후:

```bash
terraform apply -auto-approve
terraform output api_endpoint
```

Expected: `Apply complete!` + `https://xxxx.execute-api.ap-northeast-2.amazonaws.com` 형태 출력. 이 시점에는 ECR에 이미지가 없어 컨테이너는 뜨지 않는 게 정상 (deploy.sh가 pull 실패 시 정상 종료).

- [ ] **Step 5: 인프라 저장소 GitHub push**

```bash
cd ~/aws-infra
gh repo create gunb0s/aws-infra --private --source . --push
```

Expected: `Created repository gunb0s/aws-infra` + push 완료

- [ ] **Step 6: 인스턴스 user_data 성공 확인**

```bash
INSTANCE_ID=$(cd ~/aws-infra/kkoma-slack && terraform output -raw instance_id)
aws ssm send-command --profile personal --region ap-northeast-2 \
  --document-name AWS-RunShellScript \
  --instance-ids "$INSTANCE_ID" \
  --parameters 'commands=["test -x /opt/kkoma/deploy.sh && test -f /opt/kkoma/.env && echo READY"]' \
  --query Command.CommandId --output text
```

몇 초 후 `aws ssm get-command-invocation --profile personal --region ap-northeast-2 --command-id <위 출력> --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text`
Expected: `READY` (SSM Agent 부팅에 1~2분 걸릴 수 있으니 실패 시 잠시 후 재시도)

---

### Task 7: 앱 저장소 push → 첫 자동 배포

- [ ] **Step 1: GitHub 저장소 생성 + push**

```bash
cd /Users/gunbos/Documents/Codex/2026-06-01/new-chat/outputs/kkoma-slack
git add -A && git status --short
```

스테이징 목록에 `.env`, `data/*.db`가 없는지 확인 후:

```bash
git commit -m "feat: kkoma-slack 초기 코드"
gh repo create gunb0s/kkoma-slack --public --source . --push
```

Expected: 저장소 생성 + push 완료, Actions 자동 트리거

- [ ] **Step 2: Actions 첫 배포 확인**

Run: `gh run watch --repo gunb0s/kkoma-slack --exit-status`
Expected: deploy 워크플로우 `✓` 성공 (빌드 → ECR push → SSM 재배포)

- [ ] **Step 3: 엔드포인트 검증**

```bash
API=$(cd ~/aws-infra/kkoma-slack && terraform output -raw api_endpoint)
curl -s "$API/healthz"
curl -s -o /dev/null -w "%{http_code}" -X POST "$API/slack/commands" -d "text=test"
```

Expected: healthz는 `{"ok": true, "engine_mode": "remote", ...}`, 서명 없는 POST는 `401`

---

### Task 8: Slack 연결 (사용자 수동 단계)

- [ ] **Step 1: Slack command URL 교체 — 사용자 직접**

https://api.slack.com/apps → 해당 앱 → Slash Commands → `/kkoma` → Request URL을 `https://<api_endpoint>/slack/commands`로 교체 후 저장.

- [ ] **Step 2: 종단 검증**

Slack 채널에서 `/kkoma status` 실행.
Expected: 진행 현황 응답이 채널에 표시됨.

- [ ] **Step 3: 마무리 commit/push**

```bash
cd /Users/gunbos/Documents/Codex/2026-06-01/new-chat/outputs/kkoma-slack
git push origin main
cd ~/aws-infra && git push origin main
```

(이미 push된 상태면 `Everything up-to-date`)
