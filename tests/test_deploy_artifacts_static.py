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
            "IMAGE_DISTRIBUTION_MODE",
            "LOCAL_IMAGE_REPOSITORY",
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
    assert re.search(r"agent\s*\{\s*label\s+['\"]\\?\$\{?params\.BUILD_AGENT_LABEL", jenkinsfile), (
        "Toolchain, test, build, and push stages must run on BUILD_AGENT_LABEL so "
        "the current Docker-less ENM-hosted Jenkins controller cannot satisfy the "
        "deployment preflight by accident"
    )

    _assert_contains_all(
        jenkinsfile,
        ["uv run pytest -q", "docker build", "docker push", "IMAGE_REF", "MOVING_ALIAS_REF"],
        label="test/build/optional-push flow",
    )

    forbidden_image_ref_assignments = re.findall(
        r"(?m)^\s*(?:env\.)?IMAGE_REF\s*=\s*['\"][^'\"]*(?:latest|dev-latest|prod-latest)[^'\"]*['\"]",
        jenkinsfile,
    )
    assert not forbidden_image_ref_assignments, (
        "Deployment IMAGE_REF must be immutable; moving latest aliases may be pushed "
        "only as convenience aliases"
    )


def test_jenkinsfile_routes_main_to_prod_and_non_main_to_dev_targets() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")

    _assert_contains_all(
        jenkinsfile,
        [
            "env.DEPLOY_BRANCH == 'main' ? 'prod' : 'dev'",
            "/home/ameforce/slack-emoji-tailor-prod",
            "/home/ameforce/slack-emoji-tailor-dev",
            "'3100' : '18082'",
            "https://emoji.enmsoftware.com/healthz",
            "https://dev.emoji.enmsoftware.com/healthz",
            "slack-emoji-tailor-prod",
            "slack-emoji-tailor-dev",
        ],
        label="branch-aware deploy routing",
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
    forbidden_on_host_commands = [r"\bssh\b", r"\bscp\b", r"\bsshpass\b"]
    for pattern in forbidden_on_host_commands:
        assert not re.search(pattern, public_health, flags=re.IGNORECASE), (
            "Public health proof must come from PUBLIC_CHECK_AGENT_LABEL, not from "
            f"an SSH/on-host command matching {pattern!r}"
        )


def test_compose_deploy_file_uses_image_localhost_port_and_healthcheck() -> None:
    _require_implementation_lanes_integrated()
    compose = _read("docker-compose.dev.deploy.yml")

    assert "${IMAGE_REF}" in compose, "deploy compose must use the immutable IMAGE_REF variable"
    assert not re.search(r"(?m)^\s*build\s*:", compose), "deploy compose must not build on enm-server"
    assert "container_name:" not in compose, "dev/prod compose projects must not share a fixed container name"
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
            "--password-stdin",
            "DOCKER_CONFIG",
            "IMAGE_REF",
            "NO_PREVIOUS_IMAGE_AVAILABLE",
            "SKIP_IMAGE_PULL",
            "/healthz",
        ],
        label="deploy script guardrails",
    )
    for label, pattern in {
        "compose pull": r"docker\s+compose\b[\s\S]*?\bpull\b",
        "compose up": r"docker\s+compose\b[\s\S]*?\bup\s+-d\b",
        "first-install compose down": r"docker\s+compose\b[\s\S]*?\bdown\b",
        "previous image marker": r"PREVIOUS_IMAGE|previous[-_]image[-_]ref",
        "current image marker": r"CURRENT_IMAGE|current[-_]image[-_]ref",
    }.items():
        assert re.search(pattern, deploy_script, flags=re.IGNORECASE), (
            f"deploy script is missing required guardrail: {label}"
        )
    _assert_contains_all(
        rollback_script,
        ["PREVIOUS_IMAGE", "IMAGE_REF", "/healthz"],
        label="rollback script guardrails",
    )
    for label, pattern in {
        "rollback compose pull": r"docker\s+compose\b[\s\S]*?\bpull\b",
        "rollback compose up": r"docker\s+compose\b[\s\S]*?\bup\s+-d\b",
    }.items():
        assert re.search(pattern, rollback_script, flags=re.IGNORECASE), (
            f"rollback script is missing required guardrail: {label}"
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

    assert not re.search(r"set\s+-x", combined), (
        "deploy scripts must not enable shell tracing where Jenkins/server "
        "credentials may be in scope"
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
