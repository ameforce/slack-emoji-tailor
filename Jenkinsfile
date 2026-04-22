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
    string(name: 'PUBLIC_CHECK_AGENT_LABEL', defaultValue: 'external-http-check', description: 'Jenkins agent/probe outside enm-server used for public URL health checks.')
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
    string(name: 'DEPLOY_ALLOWED_BRANCHES', defaultValue: 'main,develop', description: 'Comma-separated branches allowed to deploy. Empty allows any branch; manual builds are allowed.')
    string(name: 'DEPLOY_HEALTHCHECK_TIMEOUT_SECONDS', defaultValue: '120', description: 'Maximum local health wait budget for the deploy script.')
    string(name: 'DEPLOY_HEALTHCHECK_INTERVAL_SECONDS', defaultValue: '5', description: 'Local health retry interval for the deploy script.')
    string(name: 'PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS', defaultValue: '15', description: 'curl --max-time budget for the public health check.')
    string(name: 'PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS', defaultValue: 'enm-server|jenkins-controller|^jenkins$', description: 'Extended grep pattern that identifies hosts forbidden for external public checks.')
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
          def branchName = (env.BRANCH_NAME ?: env.GIT_BRANCH ?: env.GIT_LOCAL_BRANCH ?: env.CHANGE_BRANCH ?: env.JOB_BASE_NAME ?: 'manual').trim()
          if (branchName.startsWith('origin/')) {
            branchName = branchName.substring('origin/'.length())
          }
          if (branchName.startsWith('refs/heads/')) {
            branchName = branchName.substring('refs/heads/'.length())
          }
          if (branchName == 'manual' || branchName == env.JOB_BASE_NAME) {
            def jobBaseName = (env.JOB_BASE_NAME ?: '').trim()
            if (jobBaseName.endsWith('-main')) {
              branchName = 'main'
            } else if (jobBaseName.endsWith('-develop')) {
              branchName = 'develop'
            }
          }
          env.DEPLOY_BRANCH = branchName ?: 'manual'

          env.DEPLOY_ENVIRONMENT = env.DEPLOY_BRANCH == 'main' ? 'prod' : 'dev'
          def prodTarget = env.DEPLOY_ENVIRONMENT == 'prod'
          env.EFFECTIVE_DEPLOY_HOST = valueOrDefault(params.DEPLOY_HOST, 'enmsoftware.com')
          env.EFFECTIVE_DEPLOY_SSH_USER = valueOrDefault(params.DEPLOY_SSH_USER, 'ameforce')
          env.EFFECTIVE_DEPLOY_PATH = valueOrDefault(params.DEPLOY_PATH, prodTarget ? '/home/ameforce/slack-emoji-tailor-prod' : '/home/ameforce/slack-emoji-tailor-dev')
          env.EFFECTIVE_DEPLOY_APP_PORT = valueOrDefault(params.DEPLOY_APP_PORT, prodTarget ? '3100' : '18082')
          env.EFFECTIVE_DEPLOY_COMPOSE_PROJECT = valueOrDefault(params.DEPLOY_COMPOSE_PROJECT, prodTarget ? 'slack-emoji-tailor-prod' : 'slack-emoji-tailor-dev')
          env.PUBLIC_HEALTHCHECK_URL_RESOLVED = valueOrDefault(params.PUBLIC_HEALTHCHECK_URL, prodTarget ? 'https://emoji.enmsoftware.com/healthz' : 'https://dev.emoji.enmsoftware.com/healthz')
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
          env.IMAGE_TAG = "${env.DEPLOY_ENVIRONMENT}-${env.GIT_COMMIT_SHORT}-${env.BUILD_NUMBER}"
          env.IMAGE_REF = "${env.IMAGE_REPOSITORY}:${env.IMAGE_TAG}"
          env.MOVING_ALIAS_REF = "${env.IMAGE_REPOSITORY}:${env.DEPLOY_ENVIRONMENT}-latest"
          env.PUBLIC_HEALTH_FAILED = 'false'

          if (env.IMAGE_REF ==~ /.*:(latest|dev-latest|prod-latest)$/) {
            error('Deploy IMAGE_REF must be immutable and must not be a moving latest alias.')
          }

          writeFile file: 'image-ref.txt', text: "IMAGE_REF=${env.IMAGE_REF}\nMOVING_ALIAS_REF=${env.MOVING_ALIAS_REF}\nIMAGE_DISTRIBUTION_MODE=${env.IMAGE_DISTRIBUTION_MODE_RESOLVED}\nDEPLOY_ENVIRONMENT=${env.DEPLOY_ENVIRONMENT}\nGIT_COMMIT=${env.GIT_COMMIT_RESOLVED}\nBRANCH=${env.DEPLOY_BRANCH}\n"
          echo "Resolved ${env.DEPLOY_ENVIRONMENT} immutable image ref: ${env.IMAGE_REF}"
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
APP_VERSION="$(uv run python -c 'from app.versioning import get_display_version; print(get_display_version())')"
: "${APP_VERSION:?APP_VERSION is required}"
docker build \
  --pull \
  --build-arg "SLACK_EMOJI_TAILOR_VERSION=${APP_VERSION}" \
  --label "org.opencontainers.image.revision=${GIT_COMMIT_RESOLVED}" \
  --label "org.opencontainers.image.source=${JOB_URL:-jenkins}" \
  --label "org.opencontainers.image.version=${APP_VERSION}" \
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
          requireParam('PUBLIC_CHECK_AGENT_LABEL', params.PUBLIC_CHECK_AGENT_LABEL)

          if (!(env.EFFECTIVE_DEPLOY_APP_PORT ==~ /^[0-9]+$/)) {
            error('DEPLOY_APP_PORT must be numeric.')
          }
          def port = env.EFFECTIVE_DEPLOY_APP_PORT.toInteger()
          if (port < 1024 || port > 65535) {
            error('DEPLOY_APP_PORT must be in the non-privileged TCP port range 1024-65535.')
          }

          def allowedBranches = params.DEPLOY_ALLOWED_BRANCHES.split(',').collect { it.trim() }.findAll { it }
          def canDeployBranch = env.DEPLOY_BRANCH == 'manual' || allowedBranches.isEmpty() || allowedBranches.contains(env.DEPLOY_BRANCH)
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
public_check_agent_label=${PUBLIC_CHECK_AGENT_LABEL}
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
bash "$DEPLOY_SCRIPT"
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

    stage('External Public Health') {
      agent { label "${params.PUBLIC_CHECK_AGENT_LABEL}" }
      when {
        beforeAgent true
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
        }
      }
      environment {
        PUBLIC_HEALTHCHECK_URL = "${env.PUBLIC_HEALTHCHECK_URL_RESOLVED}"
        PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS = "${params.PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS}"
        PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS = "${params.PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS}"
      }
      steps {
        script {
          try {
            sh '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p deploy-evidence
{
  echo "hostname=$(hostname)"
  echo "fqdn=$(hostname -f 2>/dev/null || true)"
  hostnamectl 2>/dev/null || true
} | tee deploy-evidence/public-check-host-proof.txt

if [ -n "${PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS:-}" ] && grep -Eiq "$PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS" deploy-evidence/public-check-host-proof.txt; then
  echo "Public health check appears to be running on a forbidden host/controller pattern: ${PUBLIC_CHECK_FORBIDDEN_HOST_PATTERNS}" | tee deploy-evidence/public-health-failure.txt
  exit 31
fi

curl --fail --show-error --silent --location --max-time "$PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS" "$PUBLIC_HEALTHCHECK_URL" | tee deploy-evidence/public-health-response.txt
'''
          } catch (err) {
            env.PUBLIC_HEALTH_FAILED = 'true'
            echo "External public health failed; rollback stage will run on the build agent. ${err}"
          } finally {
            archiveArtifacts artifacts: 'deploy-evidence/**', allowEmptyArchive: true, fingerprint: true
          }
        }
      }
    }

    stage('Auto Rollback After Public Health Failure') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
          expression { return env.PUBLIC_HEALTH_FAILED == 'true' }
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
bash "$ROLLBACK_SCRIPT" | tee deploy-evidence/rollback-after-public-health.txt
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

    stage('Fail Deployment After Public Health Rollback') {
      when {
        allOf {
          expression { return params.RUN_DEPLOY }
          expression { return !params.DEPLOY_DRY_RUN }
          expression { return env.PUBLIC_HEALTH_FAILED == 'true' }
        }
      }
      steps {
        error('External public health failed. Jenkins attempted rollback; failing the original deployment build by design.')
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
      echo 'Jenkins deployment pipeline finished. See archived image/deploy/health/rollback evidence for proof.'
    }
  }
}

String valueOrDefault(Object value, String fallback) {
  def normalized = value == null ? '' : value.toString().trim()
  return normalized ? normalized : fallback
}

void requireParam(String name, Object value) {
  if (value == null || value.toString().trim().isEmpty()) {
    error("${name} parameter is required.")
  }
}
