pipeline {
  agent { label "${params.BUILD_AGENT_LABEL}" }

  options {
    disableConcurrentBuilds()
    timeout(time: 90, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '30'))
    skipDefaultCheckout(true)
  }

  parameters {
    string(name: 'BUILD_AGENT_LABEL', defaultValue: 'docker-build', description: 'Jenkins agent label with python3, preinstalled uv, Docker CLI/daemon, and docker buildx. The ENM controller is not sufficient.')
    booleanParam(name: 'RUN_DEPLOY', defaultValue: false, description: 'When true, deploy the pushed immutable image through Jenkins SSH after build/test/push succeeds.')
    booleanParam(name: 'DEPLOY_DRY_RUN', defaultValue: true, description: 'When true with RUN_DEPLOY, archive the deploy preview but do not mutate enm-server.')

    string(name: 'DEPLOY_HOST', defaultValue: 'enmsoftware.com', description: 'Target ENM server SSH host.')
    string(name: 'DEPLOY_SSH_USER', defaultValue: 'ameforce', description: 'Target ENM server SSH user.')
    string(name: 'DEPLOY_PATH', defaultValue: '', description: 'Optional override. Auto: main -> /home/ameforce/slack-emoji-tailor-prod, non-main -> /home/ameforce/slack-emoji-tailor-dev.')
    string(name: 'DEPLOY_SSH_CREDENTIALS_ID', defaultValue: 'enm-server-ssh-key', description: 'Jenkins SSH private key credential ID for enm-server.')
    string(name: 'DEPLOY_SSH_OPTS', defaultValue: '-o BatchMode=yes -o StrictHostKeyChecking=accept-new', description: 'Additional ssh/scp options passed to deploy scripts.')

    choice(name: 'IMAGE_DISTRIBUTION_MODE', choices: ['remote-build', 'registry'], description: 'remote-build builds the image on the Docker-capable Jenkins agent that is also the deploy host; registry pushes/pulls through REGISTRY_* credentials.')
    string(name: 'LOCAL_IMAGE_REPOSITORY', defaultValue: 'slack-emoji-tailor', description: 'Local Docker image repository used when IMAGE_DISTRIBUTION_MODE=remote-build.')
    string(name: 'REGISTRY_URL', defaultValue: '', description: 'Docker registry hostname for registry mode, for example ghcr.io or registry.example.com.')
    string(name: 'REGISTRY_IMAGE', defaultValue: 'enm/slack-emoji-tailor', description: 'Registry image path without tag. Combined with REGISTRY_URL and immutable build tag in registry mode.')
    string(name: 'REGISTRY_CREDENTIALS_ID', defaultValue: '', description: 'Jenkins username/password credential ID for registry push in registry mode.')
    string(name: 'REGISTRY_PULL_CREDENTIALS_ID', defaultValue: '', description: 'Optional Jenkins username/password credential ID passed to the remote deploy script for isolated docker login on enm-server in registry mode.')

    string(name: 'DEPLOY_APP_PORT', defaultValue: '', description: 'Optional override. Auto: main -> 3100, non-main -> 18082.')
    string(name: 'PUBLIC_HEALTHCHECK_URL', defaultValue: '', description: 'Optional override. Auto: main -> https://emoji.enmsoftware.com/healthz, non-main -> https://dev.emoji.enmsoftware.com/healthz.')
    string(name: 'LOCAL_HEALTHCHECK_URL', defaultValue: '', description: 'Optional override. Auto: http://127.0.0.1:${DEPLOY_APP_PORT}/healthz.')
    string(name: 'DEPLOY_COMPOSE_PROJECT', defaultValue: '', description: 'Optional override. Auto: main -> slack-emoji-tailor-prod, non-main -> slack-emoji-tailor-dev.')
    string(name: 'DEPLOY_ALLOWED_BRANCHES', defaultValue: '', description: 'Emergency narrowing only. Empty allows any branch after branch identity is resolved; branch identity still fixes prod/dev targets.')
    string(name: 'DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS', defaultValue: '120', description: 'Maximum local health wait budget for the deploy script.')
    string(name: 'DEPLOY_HEALTHCHECK_INTERVAL_SECONDS', defaultValue: '5', description: 'Local health retry interval for the deploy script.')
    string(name: 'PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS', defaultValue: '15', description: 'curl --max-time budget for same-server public URL/API smoke health checks.')
  }

  environment {
    PYTHONUNBUFFERED = '1'
    DOCKER_BUILDKIT = '1'
    DEPLOY_COMPOSE_FILE = 'docker-compose.dev.deploy.yml'
    DEPLOY_SCRIPT = 'scripts/deploy/jenkins-enm-deploy.sh'
    ROLLBACK_SCRIPT = 'scripts/deploy/jenkins-enm-rollback.sh'
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Resolve Immutable Image Ref') {
      steps {
        script {
          requireParam('BUILD_AGENT_LABEL', params.BUILD_AGENT_LABEL)
          env.DEPLOY_BRANCH = resolveDeployBranch()

          env.DEPLOY_ENVIRONMENT = env.DEPLOY_BRANCH == 'main' ? 'prod' : 'dev'
          def prodTarget = env.DEPLOY_ENVIRONMENT == 'prod'
          def canonicalDeployPath = prodTarget ? '/home/ameforce/slack-emoji-tailor-prod' : '/home/ameforce/slack-emoji-tailor-dev'
          def canonicalDeployAppPort = prodTarget ? '3100' : '18082'
          def canonicalDeployComposeProject = prodTarget ? 'slack-emoji-tailor-prod' : 'slack-emoji-tailor-dev'
          def canonicalPublicHealthcheckUrl = prodTarget ? 'https://emoji.enmsoftware.com/healthz' : 'https://dev.emoji.enmsoftware.com/healthz'
          env.EFFECTIVE_DEPLOY_HOST = valueOrDefault(params.DEPLOY_HOST, 'enmsoftware.com')
          env.EFFECTIVE_DEPLOY_SSH_USER = valueOrDefault(params.DEPLOY_SSH_USER, 'ameforce')
          env.EFFECTIVE_DEPLOY_PATH = branchTargetValue('DEPLOY_PATH', params.DEPLOY_PATH, canonicalDeployPath, env.DEPLOY_BRANCH)
          env.EFFECTIVE_DEPLOY_APP_PORT = branchTargetValue('DEPLOY_APP_PORT', params.DEPLOY_APP_PORT, canonicalDeployAppPort, env.DEPLOY_BRANCH)
          env.EFFECTIVE_DEPLOY_COMPOSE_PROJECT = branchTargetValue('DEPLOY_COMPOSE_PROJECT', params.DEPLOY_COMPOSE_PROJECT, canonicalDeployComposeProject, env.DEPLOY_BRANCH)
          env.PUBLIC_HEALTHCHECK_URL_RESOLVED = branchTargetValue('PUBLIC_HEALTHCHECK_URL', params.PUBLIC_HEALTHCHECK_URL, canonicalPublicHealthcheckUrl, env.DEPLOY_BRANCH)
          env.LOCAL_HEALTHCHECK_URL_RESOLVED = valueOrDefault(params.LOCAL_HEALTHCHECK_URL, "http://127.0.0.1:${env.EFFECTIVE_DEPLOY_APP_PORT}/healthz")

          def imageMode = valueOrDefault(params.IMAGE_DISTRIBUTION_MODE, 'remote-build')
          if (!(imageMode in ['remote-build', 'registry'])) {
            error("Unsupported IMAGE_DISTRIBUTION_MODE=${imageMode}.")
          }
          env.IMAGE_DISTRIBUTION_MODE_RESOLVED = imageMode
          env.SKIP_IMAGE_PULL_RESOLVED = imageMode == 'remote-build' ? 'true' : 'false'

          if (imageMode == 'registry') {
            requireParam('REGISTRY_URL', params.REGISTRY_URL)
            requireParam('REGISTRY_IMAGE', params.REGISTRY_IMAGE)
            requireParam('REGISTRY_CREDENTIALS_ID', params.REGISTRY_CREDENTIALS_ID)
            def registryUrl = params.REGISTRY_URL.trim().replaceAll('/+$', '')
            def registryImage = params.REGISTRY_IMAGE.trim().replaceAll('^/+', '')
            env.IMAGE_REPOSITORY = "${registryUrl}/${registryImage}"
          } else {
            env.IMAGE_REPOSITORY = valueOrDefault(params.LOCAL_IMAGE_REPOSITORY, 'slack-emoji-tailor')
          }

          env.GIT_COMMIT_RESOLVED = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
          env.GIT_COMMIT_SHORT = sh(returnStdout: true, script: 'git rev-parse --short=12 HEAD').trim()
          sh 'git fetch --force --tags origin'
          env.GIT_TAG_VERSION = sh(returnStdout: true, script: "git describe --tags --dirty --match 'v[0-9]*'").trim()
          if (!env.GIT_TAG_VERSION) {
            error('GIT_TAG_VERSION must be resolved from git tags.')
          }
          env.APP_DISPLAY_VERSION = sh(returnStdout: true, script: '''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import os
from app.versioning import derive_display_version_from_describe

version = derive_display_version_from_describe(os.environ["GIT_TAG_VERSION"])
if not version:
    raise SystemExit("Unable to derive display version from git tag metadata.")
print(version)
PY
''').trim()
          env.IMAGE_TAG = "${env.DEPLOY_ENVIRONMENT}-${env.GIT_COMMIT_SHORT}-${env.BUILD_NUMBER}"
          env.IMAGE_REF = "${env.IMAGE_REPOSITORY}:${env.IMAGE_TAG}"
          env.MOVING_ALIAS_REF = "${env.IMAGE_REPOSITORY}:${env.DEPLOY_ENVIRONMENT}-latest"
          env.POST_DEPLOY_SMOKE_FAILED = 'false'

          if (env.IMAGE_REF ==~ /.*:(latest|dev-latest|prod-latest)$/) {
            error('Deploy IMAGE_REF must be immutable and must not be a moving latest alias.')
          }

          writeFile file: 'image-ref.txt', text: "IMAGE_REF=${env.IMAGE_REF}\nMOVING_ALIAS_REF=${env.MOVING_ALIAS_REF}\nIMAGE_DISTRIBUTION_MODE=${env.IMAGE_DISTRIBUTION_MODE_RESOLVED}\nDEPLOY_ENVIRONMENT=${env.DEPLOY_ENVIRONMENT}\nGIT_COMMIT=${env.GIT_COMMIT_RESOLVED}\nGIT_TAG_VERSION=${env.GIT_TAG_VERSION}\nAPP_DISPLAY_VERSION=${env.APP_DISPLAY_VERSION}\nBRANCH=${env.DEPLOY_BRANCH}\n"
          echo "Resolved ${env.DEPLOY_ENVIRONMENT} immutable image ref: ${env.IMAGE_REF}"
          echo "Resolved git tag version: ${env.GIT_TAG_VERSION}"
          echo "Resolved display version: ${env.APP_DISPLAY_VERSION}"
        }
      }
    }

    stage('Toolchain Preflight') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
python3 --version
if ! command -v uv >/dev/null 2>&1; then
  python3 -m venv .jenkins-uv
  . .jenkins-uv/bin/activate
  python -m pip install --upgrade pip uv
fi
PATH="$PWD/.jenkins-uv/bin:$PATH"
uv --version
docker version
docker buildx version
'''
      }
    }

    stage('Test') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
PATH="$PWD/.jenkins-uv/bin:$PATH"
uv sync --frozen --dev
uv run pytest -q
'''
      }
    }

    stage('Docker Build') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
: "${IMAGE_REF:?IMAGE_REF is required}"
: "${MOVING_ALIAS_REF:?MOVING_ALIAS_REF is required}"
PATH="$PWD/.jenkins-uv/bin:$PATH"
: "${GIT_TAG_VERSION:?GIT_TAG_VERSION is required}"
docker build \
  --pull \
  --build-arg "APP_GIT_TAG_VERSION=${GIT_TAG_VERSION}" \
  --label "org.opencontainers.image.revision=${GIT_COMMIT_RESOLVED}" \
  --label "org.opencontainers.image.source=${JOB_URL:-jenkins}" \
  --label "org.opencontainers.image.version=${APP_DISPLAY_VERSION}" \
  -t "$IMAGE_REF" \
  -t "$MOVING_ALIAS_REF" \
  .
'''
      }
    }

    stage('Push Image') {
      when {
        expression { return env.IMAGE_DISTRIBUTION_MODE_RESOLVED == 'registry' }
      }
      steps {
        withCredentials([usernamePassword(credentialsId: params.REGISTRY_CREDENTIALS_ID, usernameVariable: 'REGISTRY_USERNAME', passwordVariable: 'REGISTRY_PASSWORD')]) {
          sh '''#!/usr/bin/env bash
set -euo pipefail
: "${REGISTRY_URL:?REGISTRY_URL is required}"
: "${IMAGE_REF:?IMAGE_REF is required}"
: "${MOVING_ALIAS_REF:?MOVING_ALIAS_REF is required}"
DOCKER_CONFIG_DIR="$(mktemp -d)"
trap 'rm -rf "$DOCKER_CONFIG_DIR"' EXIT
export DOCKER_CONFIG="$DOCKER_CONFIG_DIR"
printf '%s' "$REGISTRY_PASSWORD" | docker login "$REGISTRY_URL" --username "$REGISTRY_USERNAME" --password-stdin >/dev/null
docker push "$IMAGE_REF"
docker push "$MOVING_ALIAS_REF"
docker image inspect --format='{{index .RepoDigests 0}}' "$IMAGE_REF" > image-digest.txt 2>/dev/null || true
docker logout "$REGISTRY_URL" >/dev/null 2>&1 || true
'''
        }
      }
    }

    stage('Deploy Gate') {
      when {
        expression { return params.RUN_DEPLOY }
      }
      steps {
        script {
          requireParam('DEPLOY_HOST', env.EFFECTIVE_DEPLOY_HOST)
          requireParam('DEPLOY_SSH_USER', env.EFFECTIVE_DEPLOY_SSH_USER)
          requireParam('DEPLOY_PATH', env.EFFECTIVE_DEPLOY_PATH)
          requireParam('DEPLOY_SSH_CREDENTIALS_ID', params.DEPLOY_SSH_CREDENTIALS_ID)
          requireParam('DEPLOY_APP_PORT', env.EFFECTIVE_DEPLOY_APP_PORT)
          requireParam('DEPLOY_COMPOSE_PROJECT', env.EFFECTIVE_DEPLOY_COMPOSE_PROJECT)
          requireParam('PUBLIC_HEALTHCHECK_URL', env.PUBLIC_HEALTHCHECK_URL_RESOLVED)

          if (!(env.EFFECTIVE_DEPLOY_APP_PORT ==~ /^[0-9]+$/)) {
            error('DEPLOY_APP_PORT must be numeric.')
          }
          def port = env.EFFECTIVE_DEPLOY_APP_PORT.toInteger()
          if (port < 1024 || port > 65535) {
            error('DEPLOY_APP_PORT must be in the non-privileged TCP port range 1024-65535.')
          }

          def allowedBranches = params.DEPLOY_ALLOWED_BRANCHES.split(',').collect { it.trim() }.findAll { it }
          def canDeployBranch = allowedBranches.isEmpty() || allowedBranches.contains(env.DEPLOY_BRANCH)
          if (!canDeployBranch) {
            error("Branch ${env.DEPLOY_BRANCH} is outside DEPLOY_ALLOWED_BRANCHES=${params.DEPLOY_ALLOWED_BRANCHES}.")
          }
        }
      }
    }

    stage('Deploy Preview') {
      when {
        expression { return params.RUN_DEPLOY }
      }
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p deploy-evidence
cat > deploy-preview.txt <<PREVIEW
image_ref=${IMAGE_REF}
moving_alias_ref=${MOVING_ALIAS_REF}
image_distribution_mode=${IMAGE_DISTRIBUTION_MODE_RESOLVED}
deploy_environment=${DEPLOY_ENVIRONMENT}
deploy_dry_run=${DEPLOY_DRY_RUN}
target=${EFFECTIVE_DEPLOY_SSH_USER}@${EFFECTIVE_DEPLOY_HOST}
deploy_path=${EFFECTIVE_DEPLOY_PATH}
compose_file=${DEPLOY_COMPOSE_FILE}
compose_project=${EFFECTIVE_DEPLOY_COMPOSE_PROJECT}
app_host_port=${EFFECTIVE_DEPLOY_APP_PORT}
local_health_url=${LOCAL_HEALTHCHECK_URL_RESOLVED}
public_health_url=${PUBLIC_HEALTHCHECK_URL_RESOLVED}
public_smoke_scope=same-server
public_smoke_external_proof=false
PREVIEW
cp deploy-preview.txt deploy-evidence/deploy-preview.txt
cat deploy-preview.txt
'''
        archiveArtifacts artifacts: 'deploy-preview.txt,deploy-evidence/**,image-ref.txt,image-digest.txt', allowEmptyArchive: true, fingerprint: true
      }
    }

    stage('Deploy via Jenkins SSH and Local Health') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
        }
      }
      steps {
        script {
          def bindings = [
            sshUserPrivateKey(credentialsId: params.DEPLOY_SSH_CREDENTIALS_ID, keyFileVariable: 'DEPLOY_SSH_KEY', usernameVariable: 'DEPLOY_SSH_USER_FROM_CRED')
          ]
          if (params.REGISTRY_PULL_CREDENTIALS_ID?.trim()) {
            bindings.add(usernamePassword(credentialsId: params.REGISTRY_PULL_CREDENTIALS_ID, usernameVariable: 'REGISTRY_PULL_USERNAME', passwordVariable: 'REGISTRY_PULL_PASSWORD'))
          }

          withCredentials(bindings) {
            withEnv([
              "IMAGE_REF=${env.IMAGE_REF}",
              "APP_HOST_PORT=${env.EFFECTIVE_DEPLOY_APP_PORT}",
              "DEPLOY_HOST=${env.EFFECTIVE_DEPLOY_HOST}",
              "DEPLOY_SSH_USER=${env.EFFECTIVE_DEPLOY_SSH_USER}",
              "DEPLOY_PATH=${env.EFFECTIVE_DEPLOY_PATH}",
              "DEPLOY_SSH_OPTS=${params.DEPLOY_SSH_OPTS}",
              "DEPLOY_COMPOSE_FILE=${env.DEPLOY_COMPOSE_FILE}",
              "DEPLOY_COMPOSE_PROJECT=${env.EFFECTIVE_DEPLOY_COMPOSE_PROJECT}",
              "LOCAL_HEALTHCHECK_URL=${env.LOCAL_HEALTHCHECK_URL_RESOLVED}",
              "DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS=${params.DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS}",
              "DEPLOY_HEALTHCHECK_INTERVAL_SECONDS=${params.DEPLOY_HEALTHCHECK_INTERVAL_SECONDS}",
              "REGISTRY_URL=${params.REGISTRY_URL}",
              "REGISTRY_PULL_CREDENTIALS_ID=${params.REGISTRY_PULL_CREDENTIALS_ID}",
              "SKIP_IMAGE_PULL=${env.SKIP_IMAGE_PULL_RESOLVED}",
              'DEPLOY_DRY_RUN=false'
            ]) {
              sh '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p deploy-evidence
bash "$DEPLOY_SCRIPT" 2>&1 | tee deploy-evidence/deploy-script.log
'''
            }
          }
        }
      }
    }

    stage('Local Health Evidence') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
        }
      }
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p deploy-evidence
if [ -f deploy-evidence/local-health.txt ]; then
  cat deploy-evidence/local-health.txt
else
  echo "Local health is enforced by ${DEPLOY_SCRIPT}; no separate local-health.txt artifact was produced." | tee deploy-evidence/local-health-stage.txt
fi
'''
        archiveArtifacts artifacts: 'deploy-evidence/**', allowEmptyArchive: true, fingerprint: true
      }
    }

    stage('Same-Server Public URL/API Smoke') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
        }
      }
      environment {
        PUBLIC_HEALTHCHECK_URL = "${env.PUBLIC_HEALTHCHECK_URL_RESOLVED}"
        PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS = "${params.PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS}"
      }
      steps {
        script {
          try {
            sh '''#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p deploy-evidence
: "${PUBLIC_HEALTHCHECK_URL:?PUBLIC_HEALTHCHECK_URL is required}"
: "${PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS:=15}"

printf 'scope=same-server\nexternal-proof=false\n' > deploy-evidence/public-smoke-scope.txt
echo "Same-server public URL/API smoke: route=${PUBLIC_HEALTHCHECK_URL} external-proof=false" | tee deploy-evidence/public-smoke-summary.txt

BASE_URL="${PUBLIC_HEALTHCHECK_URL%/healthz}"
if [ "$BASE_URL" = "$PUBLIC_HEALTHCHECK_URL" ]; then
  BASE_URL="${PUBLIC_HEALTHCHECK_URL%/}"
fi
GIF_FIXTURE="deploy-evidence/frame-priority-smoke.gif"
EXPECTED_SOURCE_FRAMES=159
PATH="$PWD/.jenkins-uv/bin:$PATH"
GIF_FRAME_PRIORITY_SCAN_LIMIT="$(uv run python - <<'PY'
from app.services.converter_core import GIF_FRAME_PRIORITY_SCAN_LIMIT
print(GIF_FRAME_PRIORITY_SCAN_LIMIT)
PY
)"
EXPECTED_EFFECTIVE_FRAMES="$EXPECTED_SOURCE_FRAMES"
if [ "$EXPECTED_SOURCE_FRAMES" -gt "$GIF_FRAME_PRIORITY_SCAN_LIMIT" ]; then
  EXPECTED_EFFECTIVE_FRAMES="$GIF_FRAME_PRIORITY_SCAN_LIMIT"
fi
export EXPECTED_SOURCE_FRAMES GIF_FRAME_PRIORITY_SCAN_LIMIT EXPECTED_EFFECTIVE_FRAMES

uv run python - <<'PY'
from pathlib import Path
from PIL import Image, ImageDraw

frame_count = 159
frames = []
for index in range(frame_count):
    image = Image.new(
        "RGBA",
        (64, 64),
        ((index * 3) % 256, (index * 5) % 256, (index * 7) % 256, 255),
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle((index % 32, index % 32, 63, 63), outline=(255, 255, 255, 255))
    draw.text((4, 4), str(index % 100), fill=(255, 255, 255, 255))
    frames.append(image.convert("P", palette=Image.ADAPTIVE, colors=64))

Path("deploy-evidence").mkdir(exist_ok=True)
frames[0].save(
    "deploy-evidence/frame-priority-smoke.gif",
    save_all=True,
    append_images=frames[1:],
    duration=20,
    loop=0,
    disposal=2,
    optimize=False,
)
PY

cat > deploy-evidence/public-frame-priority-expected.txt <<EOF
expected_source_frames=${EXPECTED_SOURCE_FRAMES}
gif_frame_priority_scan_limit=${GIF_FRAME_PRIORITY_SCAN_LIMIT}
expected_effective_frames=${EXPECTED_EFFECTIVE_FRAMES}
EOF

run_curl_capture_status() {
  local status_file="$1"
  shift
  local status exit_code
  set +e
  status="$(curl "$@" 2>> deploy-evidence/deploy-script.log)"
  exit_code=$?
  set -e
  printf '%s\n' "${status:-000}" > "$status_file"
  if [ "$exit_code" -ne 0 ]; then
    echo "curl failed exit_code=${exit_code} status=${status:-000} status_file=${status_file}" >> deploy-evidence/deploy-script.log
    return "$exit_code"
  fi
  printf '%s' "$status"
}

health_status="$(run_curl_capture_status deploy-evidence/public-health-status.txt \
  --show-error --silent --location --max-time "$PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS" \
  --output deploy-evidence/public-health-response.txt \
  --write-out '%{http_code}' \
  "$PUBLIC_HEALTHCHECK_URL")"
test "$health_status" = "200"

inspect_status="$(run_curl_capture_status deploy-evidence/public-inspect-status.txt \
  --show-error --silent --location --max-time 30 \
  --output deploy-evidence/public-inspect-response.json \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  "$BASE_URL/api/inspect")"
test "$inspect_status" = "200"

uv run python - <<'PY' > deploy-evidence/public-inspect-summary.json
import json
import os
from pathlib import Path

payload = json.loads(Path("deploy-evidence/public-inspect-response.json").read_text())
expected = int(os.environ["EXPECTED_SOURCE_FRAMES"])
summary = {
    "http_status": Path("deploy-evidence/public-inspect-status.txt").read_text().strip(),
    "format_name": payload.get("format_name"),
    "frame_count": payload.get("frame_count"),
    "is_animated": payload.get("is_animated"),
    "expected_source_frames": expected,
}
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
if payload.get("frame_count") != expected or payload.get("is_animated") is not True:
    raise SystemExit(f"Unexpected inspect metadata: {summary}")
PY

frames_status="$(run_curl_capture_status deploy-evidence/public-convert-frames-status.txt \
  --show-error --silent --location --max-time 60 \
  --dump-header deploy-evidence/public-convert-frames-headers.txt \
  --output deploy-evidence/public-convert-frames.gif \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  -F "max_kb=128" \
  -F "size=auto" \
  -F "fit=cover" \
  -F "max_frames=50" \
  -F "optimization_strategy=frames" \
  "$BASE_URL/api/convert")"
test "$frames_status" = "200"
grep -Eiq '^X-Optimization-Strategy:[[:space:]]*frames([[:space:]]|$)' deploy-evidence/public-convert-frames-headers.txt
grep -Eiq '^X-Effective-Max-Frames:[[:space:]]*'"$EXPECTED_EFFECTIVE_FRAMES"'([[:space:]]|$)' deploy-evidence/public-convert-frames-headers.txt
grep -Eiq '^X-Frame-Cap-Mode:[[:space:]]*' deploy-evidence/public-convert-frames-headers.txt
grep -Eiq '^X-Frame-Reduction-Reason:[[:space:]]*' deploy-evidence/public-convert-frames-headers.txt
frames_bytes="$(wc -c < deploy-evidence/public-convert-frames.gif | tr -d ' ')"
printf 'http_status=%s\nresult_bytes=%s\nmax_bytes=%s\nexpected_effective_frames=%s\n' \
  "$frames_status" "$frames_bytes" "$((128 * 1024))" "$EXPECTED_EFFECTIVE_FRAMES" \
  > deploy-evidence/public-convert-frames-summary.txt
test "$frames_bytes" -le $((128 * 1024))

tight_status="$(run_curl_capture_status deploy-evidence/public-convert-tight-status.txt \
  --show-error --silent --location --max-time 60 \
  --dump-header deploy-evidence/public-convert-tight-headers.txt \
  --output deploy-evidence/public-convert-tight.gif \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  -F "max_kb=1" \
  -F "size=auto" \
  -F "fit=cover" \
  -F "max_frames=50" \
  -F "optimization_strategy=frames" \
  "$BASE_URL/api/convert")"
test "$tight_status" = "200"
grep -Eiq '^X-Frame-Cap-Mode:[[:space:]]*' deploy-evidence/public-convert-tight-headers.txt
grep -Eiq '^X-Frame-Reduction-Reason:[[:space:]]*budget-limit([[:space:]]|$)' deploy-evidence/public-convert-tight-headers.txt
grep -Eiq '^X-Gif-Search-Exhausted:[[:space:]]*true([[:space:]]|$)' deploy-evidence/public-convert-tight-headers.txt
grep -Eiq '^X-Target-Reached:[[:space:]]*false([[:space:]]|$)' deploy-evidence/public-convert-tight-headers.txt
tight_bytes="$(wc -c < deploy-evidence/public-convert-tight.gif | tr -d ' ')"
printf 'http_status=%s\nresult_bytes=%s\nmax_bytes=%s\nexpected_effective_frames=%s\n' \
  "$tight_status" "$tight_bytes" "$((1 * 1024))" "$EXPECTED_EFFECTIVE_FRAMES" \
  > deploy-evidence/public-convert-tight-summary.txt

echo "Same-server public URL/API smoke passed for ${BASE_URL}."
'''
          } catch (err) {
            env.POST_DEPLOY_SMOKE_FAILED = 'true'
            echo "Post-deploy public smoke failed; rollback stage will run on the build agent. ${err}"
          } finally {
            archiveArtifacts artifacts: 'deploy-evidence/**', allowEmptyArchive: true, fingerprint: true
          }
        }
      }
    }

    stage('Auto Rollback After Post-Deploy Smoke Failure') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
          expression { return env.POST_DEPLOY_SMOKE_FAILED == 'true' }
        }
      }
      steps {
        script {
          def bindings = [
            sshUserPrivateKey(credentialsId: params.DEPLOY_SSH_CREDENTIALS_ID, keyFileVariable: 'DEPLOY_SSH_KEY', usernameVariable: 'DEPLOY_SSH_USER_FROM_CRED')
          ]
          if (params.REGISTRY_PULL_CREDENTIALS_ID?.trim()) {
            bindings.add(usernamePassword(credentialsId: params.REGISTRY_PULL_CREDENTIALS_ID, usernameVariable: 'REGISTRY_PULL_USERNAME', passwordVariable: 'REGISTRY_PULL_PASSWORD'))
          }

          withCredentials(bindings) {
            withEnv([
              "DEPLOY_HOST=${env.EFFECTIVE_DEPLOY_HOST}",
              "DEPLOY_SSH_USER=${env.EFFECTIVE_DEPLOY_SSH_USER}",
              "DEPLOY_PATH=${env.EFFECTIVE_DEPLOY_PATH}",
              "DEPLOY_SSH_OPTS=${params.DEPLOY_SSH_OPTS}",
              "DEPLOY_COMPOSE_FILE=${env.DEPLOY_COMPOSE_FILE}",
              "DEPLOY_COMPOSE_PROJECT=${env.EFFECTIVE_DEPLOY_COMPOSE_PROJECT}",
              "APP_HOST_PORT=${env.EFFECTIVE_DEPLOY_APP_PORT}",
              "LOCAL_HEALTHCHECK_URL=${env.LOCAL_HEALTHCHECK_URL_RESOLVED}",
              "PUBLIC_HEALTHCHECK_URL=${env.PUBLIC_HEALTHCHECK_URL_RESOLVED}",
              "REGISTRY_URL=${params.REGISTRY_URL}",
              "REGISTRY_PULL_CREDENTIALS_ID=${params.REGISTRY_PULL_CREDENTIALS_ID}",
              "SKIP_IMAGE_PULL=${env.SKIP_IMAGE_PULL_RESOLVED}"
            ]) {
              sh '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p deploy-evidence
bash "$ROLLBACK_SCRIPT" 2>&1 | tee deploy-evidence/rollback-after-post-deploy-smoke.txt
'''
            }
          }
        }
      }
      post {
        always {
          archiveArtifacts artifacts: 'deploy-evidence/**', allowEmptyArchive: true, fingerprint: true
        }
      }
    }

    stage('Fail Deployment After Post-Deploy Smoke Rollback') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
          expression { return env.POST_DEPLOY_SMOKE_FAILED == 'true' }
        }
      }
      steps {
        error('Post-deploy public smoke failed. Jenkins attempted rollback; failing the original deployment build by design.')
      }
    }

    stage('Archive Evidence') {
      steps {
        archiveArtifacts artifacts: 'image-ref.txt,image-digest.txt,deploy-preview.txt,deploy-evidence/**', allowEmptyArchive: true, fingerprint: true
      }
    }
  }

  post {
    always {
      echo 'Jenkins deployment pipeline finished. See archived image/deploy/local-health/same-server-public-smoke/rollback evidence for proof.'
    }
  }
}

String valueOrDefault(Object value, String fallback) {
  def normalized = value == null ? '' : value.toString().trim()
  return normalized ? normalized : fallback
}

String branchTargetValue(String name, Object value, String canonical, String branchName) {
  def normalized = value == null ? '' : value.toString().trim()
  if (!normalized) {
    return canonical
  }
  if (normalized != canonical) {
    error("Branch target policy mismatch: ${name}=${normalized} is not allowed for branch ${branchName}; expected ${canonical}.")
  }
  return normalized
}

String resolveDeployBranch() {
  for (candidate in [env.BRANCH_NAME, env.CHANGE_BRANCH, env.GIT_LOCAL_BRANCH, env.GIT_BRANCH]) {
    def branchName = normalizeBranchName(candidate)
    if (branchName) {
      return branchName
    }
  }
  error('Unable to resolve branch identity from Jenkins multibranch environment; refusing to choose a deploy target.')
}

String normalizeBranchName(Object value) {
  def branchName = value == null ? '' : value.toString().trim()
  if (!branchName) {
    return ''
  }
  branchName = branchName.replaceFirst(/^refs\/heads\//, '')
  branchName = branchName.replaceFirst(/^refs\/remotes\/origin\//, '')
  branchName = branchName.replaceFirst(/^origin\//, '')
  if (branchName in ['HEAD', 'detached', 'manual']) {
    return ''
  }
  return branchName
}

void requireParam(String name, Object value) {
  if (value == null || value.toString().trim().isEmpty()) {
    error("${name} parameter is required.")
  }
}
