import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

IMPLEMENTATION_ARTIFACTS = [
    "Jenkinsfile",
    "docker-compose.dev.deploy.yml",
    "scripts/deploy/jenkins-enm-deploy.sh",
    "scripts/deploy/jenkins-enm-rollback.sh",
]


def _require_implementation_lanes_integrated() -> None:
    if not any((ROOT / path).exists() for path in IMPLEMENTATION_ARTIFACTS):
        pytest.skip(
            "deployment implementation artifacts are supplied by parallel worker "
            "lanes; static guardrail tests activate once any implementation artifact "
            "is integrated"
        )


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.exists(), f"Missing required deploy artifact: {relative_path}"
    return path.read_text(encoding="utf-8")


def _assert_contains_all(text: str, required: list[str], *, label: str) -> None:
    missing = [item for item in required if item not in text]
    assert not missing, f"{label} is missing required text: {missing}"


def _stage_body(jenkinsfile: str, stage_name_fragment: str) -> str:
    match = re.search(
        rf"stage\s*\(\s*['\"][^'\"]*{re.escape(stage_name_fragment)}[^'\"]*['\"]\s*\)",
        jenkinsfile,
        flags=re.IGNORECASE,
    )
    assert match, f"Jenkinsfile is missing a stage containing {stage_name_fragment!r}"
    next_stage = re.search(r"\n\s*stage\s*\(", jenkinsfile[match.end() :])
    end = match.end() + next_stage.start() if next_stage else len(jenkinsfile)
    return jenkinsfile[match.start() : end]


def test_jenkinsfile_declares_required_parameters_and_core_stages() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")

    _assert_contains_all(
        jenkinsfile,
        [
            "BUILD_AGENT_LABEL",
            "PUBLIC_CHECK_AGENT_LABEL",
            "RUN_DEPLOY",
            "DEPLOY_DRY_RUN",
            "DEPLOY_HOST",
            "DEPLOY_SSH_USER",
            "DEPLOY_PATH",
            "DEPLOY_SSH_CREDENTIALS_ID",
            "REGISTRY_URL",
            "REGISTRY_IMAGE",
            "REGISTRY_CREDENTIALS_ID",
            "REGISTRY_PULL_CREDENTIALS_ID",
            "DEPLOY_APP_PORT",
            "PUBLIC_HEALTHCHECK_URL",
            "LOCAL_HEALTHCHECK_URL",
            "DEPLOY_COMPOSE_PROJECT",
            "DEPLOY_ALLOWED_BRANCHES",
        ],
        label="Jenkinsfile parameters",
    )

    assert "disableConcurrentBuilds" in jenkinsfile, (
        "Jenkinsfile must serialize deploy builds instead of allowing concurrent "
        "remote compose mutations"
    )

    for stage_name in [
        "Checkout",
        "Resolve",
        "Toolchain Preflight",
        "Test",
        "Build",
        "Push",
        "Deploy Preview",
        "Deploy",
        "Local Health",
        "Public Health",
        "Rollback",
        "Archive",
    ]:
        _stage_body(jenkinsfile, stage_name)


def test_jenkinsfile_fails_fast_on_build_agent_tooling_and_uses_immutable_image_ref() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    preflight = _stage_body(jenkinsfile, "Toolchain Preflight")

    _assert_contains_all(
        preflight,
        ["python3 --version", "uv --version", "docker version", "docker buildx version"],
        label="build-agent preflight",
    )
    assert "params.BUILD_AGENT_LABEL" in preflight or "BUILD_AGENT_LABEL" in preflight

    _assert_contains_all(
        jenkinsfile,
        ["uv run pytest -q", "Dockerfile", "docker push", "IMAGE_REF"],
        label="test/build/push flow",
    )

    forbidden_image_ref_assignments = re.findall(
        r"(?m)^\s*(?:env\.)?IMAGE_REF\s*=\s*['\"][^'\"]*dev-latest[^'\"]*['\"]",
        jenkinsfile,
    )
    assert not forbidden_image_ref_assignments, (
        "Deployment IMAGE_REF must be immutable; dev-latest may be pushed only as a "
        "convenience alias"
    )


def test_public_health_runs_on_external_agent_not_through_enm_ssh() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    public_health = _stage_body(jenkinsfile, "Public Health")

    assert "params.PUBLIC_CHECK_AGENT_LABEL" in public_health or "PUBLIC_CHECK_AGENT_LABEL" in public_health
    _assert_contains_all(
        public_health,
        ["hostname", "curl", "PUBLIC_HEALTHCHECK_URL"],
        label="external public health stage",
    )
    assert re.search(r"enm[-_]?server|controller", public_health, re.IGNORECASE), (
        "Public health stage must fail fast when the check is running on enm-server "
        "or the Jenkins controller host"
    )
    assert "ssh" not in public_health.lower(), (
        "Public health proof must come from PUBLIC_CHECK_AGENT_LABEL, not from an "
        "SSH/on-host curl"
    )


def test_compose_deploy_file_uses_registry_image_localhost_port_and_healthcheck() -> None:
    _require_implementation_lanes_integrated()
    compose = _read("docker-compose.dev.deploy.yml")

    assert "${IMAGE_REF}" in compose, "deploy compose must use the immutable IMAGE_REF variable"
    assert not re.search(r"(?m)^\s*build\s*:", compose), "deploy compose must not build on enm-server"
    assert "127.0.0.1" in compose, "deploy compose must bind app port to localhost only"
    assert "${APP_HOST_PORT:-18082}" in compose, "deploy compose must default APP_HOST_PORT to 18082"
    assert re.search(r"127\.0\.0\.1:\$\{APP_HOST_PORT:-18082\}:8000", compose), (
        "deploy compose must expose 8000 through localhost-bound APP_HOST_PORT"
    )
    assert "restart: unless-stopped" in compose
    assert "/healthz" in compose
    assert "0.0.0.0" not in compose, "deploy compose must not expose the service publicly"


def test_deploy_scripts_have_required_safety_guards_and_no_proxy_mutations() -> None:
    _require_implementation_lanes_integrated()
    deploy_script = _read("scripts/deploy/jenkins-enm-deploy.sh")
    rollback_script = _read("scripts/deploy/jenkins-enm-rollback.sh")
    combined = f"{deploy_script}\n{rollback_script}"

    _assert_contains_all(
        deploy_script,
        [
            "flock",
            "docker compose pull",
            "docker compose up -d",
            "--password-stdin",
            "DOCKER_CONFIG",
            "IMAGE_REF",
            "PREVIOUS_IMAGE",
            "CURRENT_IMAGE",
            "NO_PREVIOUS_IMAGE_AVAILABLE",
            "docker compose down",
            "/healthz",
        ],
        label="deploy script guardrails",
    )
    _assert_contains_all(
        rollback_script,
        ["PREVIOUS_IMAGE", "IMAGE_REF", "docker compose pull", "docker compose up -d", "/healthz"],
        label="rollback script guardrails",
    )

    forbidden_mutation_patterns = {
        "nginx config path": r"/etc/nginx",
        "nginx reload/restart": r"\b(?:systemctl|service)\s+(?:reload|restart)\s+nginx\b|\bnginx\s+-s\b",
        "certbot": r"\bcertbot\b",
        "dns mutation tools": r"\b(?:nsupdate|cloudflare|route53|change-resource-record-sets)\b",
    }
    for label, pattern in forbidden_mutation_patterns.items():
        assert not re.search(pattern, combined, flags=re.IGNORECASE), (
            f"deploy scripts must not mutate DNS/Nginx/Certbot state ({label})"
        )

    secret_echo_patterns = [
        r"echo\s+.*\$\{?REGISTRY_.*(?:PASSWORD|TOKEN|SECRET)",
        r"printf\s+.*\$\{?REGISTRY_.*(?:PASSWORD|TOKEN|SECRET)",
    ]
    for pattern in secret_echo_patterns:
        assert not re.search(pattern, combined, flags=re.IGNORECASE), (
            "deploy scripts must not print registry passwords/tokens/secrets"
        )


def test_security_checklist_covers_operational_risk_controls() -> None:
    checklist = _read("docs/deploy/jenkins-dev-security-checklist.md").lower()

    required_topics = [
        "docker-capable",
        "build_agent_label",
        "registry credential",
        "password-stdin",
        "isolated docker_config",
        "public_check_agent_label",
        "outside enm-server",
        "secret redaction",
        "rollback",
        "jenkins job setup",
        "no nginx",
        "no dns",
    ]
    missing = [topic for topic in required_topics if topic not in checklist]
    assert not missing, f"security checklist is missing required topics: {missing}"
