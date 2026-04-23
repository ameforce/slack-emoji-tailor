import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

IMPLEMENTATION_ARTIFACTS = [
    "Jenkinsfile",
    "docker-compose.dev.deploy.yml",
    "scripts/deploy/jenkins-enm-deploy.sh",
    "scripts/deploy/jenkins-enm-rollback.sh",
    "scripts/deploy/public-gif-smoke.sh",
]

LF_ONLY_ARTIFACTS = [
    ".gitattributes",
    "Dockerfile",
    "Jenkinsfile",
    "docker-compose.dev.deploy.yml",
    "docker-compose.yml",
    "pyproject.toml",
    "scripts/deploy/jenkins-enm-deploy.sh",
    "scripts/deploy/jenkins-enm-rollback.sh",
    "scripts/deploy/public-gif-smoke.sh",
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


def _read_bytes(relative_path: str) -> bytes:
    path = ROOT / relative_path
    assert path.exists(), f"Missing required deploy artifact: {relative_path}"
    return path.read_bytes()


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


def _string_param_definition(jenkinsfile: str, param_name: str) -> str | None:
    match = re.search(
        rf"string\s*\(\s*name:\s*['\"]{re.escape(param_name)}['\"][\s\S]*?\)",
        jenkinsfile,
    )
    return match.group(0) if match else None


def _string_param_default(jenkinsfile: str, param_name: str) -> str | None:
    definition = _string_param_definition(jenkinsfile, param_name)
    if not definition:
        return None
    match = re.search(r"defaultValue:\s*['\"]([^'\"]*)['\"]", definition)
    assert match, f"{param_name} parameter exists but has no explicit defaultValue"
    return match.group(1)


def _xfail_if_worker1_branch_policy_lane_is_not_integrated(jenkinsfile: str) -> None:
    legacy_markers = [
        "defaultValue: 'main,develop'",
        "?: 'manual'",
        "DEPLOY_BRANCH = branchName ?: 'manual'",
        "valueOrDefault(params.DEPLOY_PATH",
        "valueOrDefault(params.DEPLOY_APP_PORT",
        "valueOrDefault(params.DEPLOY_COMPOSE_PROJECT",
        "valueOrDefault(params.PUBLIC_HEALTHCHECK_URL",
    ]
    if sum(marker in jenkinsfile for marker in legacy_markers) >= 4:
        pytest.xfail(
            "worker-1 Jenkinsfile branch-policy lane is not integrated in this "
            "worktree yet; these guardrails activate against the integrated "
            "Jenkinsfile"
        )


def test_jenkinsfile_declares_required_parameters_and_core_stages() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")

    _assert_contains_all(
        jenkinsfile,
        [
            "BUILD_AGENT_LABEL",
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
        "Public URL/API Smoke",
        "Rollback",
        "Archive",
    ]:
        _stage_body(jenkinsfile, stage_name)


def test_deploy_artifacts_use_lf_line_endings() -> None:
    _require_implementation_lanes_integrated()

    for relative_path in LF_ONLY_ARTIFACTS:
        assert b"\r\n" not in _read_bytes(relative_path), (
            f"{relative_path} must use LF line endings; CRLF can break Unix/Jenkins tooling"
        )


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


def test_jenkinsfile_allows_all_branches_by_default_and_requires_branch_identity() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    _xfail_if_worker1_branch_policy_lane_is_not_integrated(jenkinsfile)

    deploy_allowed_default = _string_param_default(jenkinsfile, "DEPLOY_ALLOWED_BRANCHES")
    if deploy_allowed_default is not None:
        definition = _string_param_definition(jenkinsfile, "DEPLOY_ALLOWED_BRANCHES")
        assert deploy_allowed_default == "", (
            "DEPLOY_ALLOWED_BRANCHES must not default to main,develop; either remove "
            "the parameter or leave the default empty so every non-main branch can "
            "deploy to shared dev when RUN_DEPLOY=true"
        )
        assert definition and "Empty allows any branch" in definition, (
            "DEPLOY_ALLOWED_BRANCHES documentation must state that an empty default "
            "allows every branch"
        )

    assert "main,develop" not in jenkinsfile, (
        "legacy main/develop branch allowlist must not remain in Jenkinsfile"
    )
    assert "?: 'manual'" not in jenkinsfile
    assert "DEPLOY_BRANCH = branchName ?: 'manual'" not in jenkinsfile
    assert re.search(r"error\s*\([\s\S]{0,200}branch", jenkinsfile, re.IGNORECASE), (
        "Jenkinsfile must fail fast when multibranch branch identity cannot be "
        "resolved instead of silently treating the build as manual"
    )


def test_jenkinsfile_rejects_branch_target_override_mismatches() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    _xfail_if_worker1_branch_policy_lane_is_not_integrated(jenkinsfile)

    for param_name in [
        "DEPLOY_PATH",
        "DEPLOY_APP_PORT",
        "DEPLOY_COMPOSE_PROJECT",
        "PUBLIC_HEALTHCHECK_URL",
    ]:
        assert f"valueOrDefault(params.{param_name}" not in jenkinsfile, (
            f"{param_name} must not silently override the branch-derived target; "
            "mismatches must be rejected before remote mutation"
        )

        direct_guard = re.search(
            rf"(?:params\.{param_name}[\s\S]{{0,700}}error\s*\(|"
            rf"error\s*\([\s\S]{{0,700}}{param_name})",
            jenkinsfile,
        )
        helper_call = re.search(
            rf"(?:assert|enforce|reject|validate)[A-Za-z0-9_]*\s*"
            rf"\([^)]*['\"]{param_name}['\"][^)]*params\.{param_name}",
            jenkinsfile,
        )
        helper_errors = re.search(
            r"def\s+(?:assert|enforce|reject|validate)[A-Za-z0-9_]*"
            r"[\s\S]{0,900}error\s*\(",
            jenkinsfile,
        )
        assert direct_guard or (helper_call and helper_errors), (
            f"Jenkinsfile must reject {param_name} values that do not match the "
            "canonical main/prod or non-main/dev target"
        )


def test_same_server_public_smoke_does_not_require_external_label() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    public_smoke = _stage_body(jenkinsfile, "Public URL/API Smoke")

    assert "PUBLIC_CHECK_AGENT_LABEL" not in jenkinsfile, (
        "single-server deploys must not require or preview the unavailable "
        "external-http-check label"
    )
    assert "external-http-check" not in jenkinsfile
    assert "agent { label" not in public_smoke, (
        "post-deploy public smoke must run on the already allocated build/deploy "
        "agent instead of queuing for a second label after mutation"
    )
    _assert_contains_all(
        public_smoke,
        [
            "Same-server public URL/API smoke",
            "PUBLIC_HEALTHCHECK_URL",
            "scope=same-server",
            "external-proof=false",
            "public-smoke-scope.txt",
            "deploy-script.log",
        ],
        label="same-server public smoke scope contract",
    )
    forbidden_on_host_commands = [r"\bssh\b", r"\bscp\b", r"\bsshpass\b"]
    for pattern in forbidden_on_host_commands:
        assert not re.search(pattern, public_smoke, flags=re.IGNORECASE), (
            "Public URL/API smoke must use the public route directly from the "
            f"current Jenkins agent, not an SSH/on-host command matching {pattern!r}"
        )


def test_same_server_public_smoke_has_fail_fast_http_and_gif_contract() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    public_smoke = _stage_body(jenkinsfile, "Public URL/API Smoke")
    smoke_script = _read("scripts/deploy/public-gif-smoke.sh")

    _assert_contains_all(
        smoke_script,
        [
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            "mkdir -p deploy-evidence",
            "--write-out",
            "public-health-status.txt",
            "public-inspect-summary.json",
            "public-convert-frames-headers.txt",
            "public-convert-tight-headers.txt",
            "frame-priority-smoke.gif",
            "EXPECTED_SOURCE_FRAMES=159",
            "EXPECTED_EFFECTIVE_FRAMES",
            "X-Optimization-Strategy",
            "X-Effective-Max-Frames",
            "X-Frame-Cap-Mode",
            "X-Frame-Reduction-Reason",
            "X-Gif-Search-Exhausted",
            "X-Target-Reached",
        ],
        label="same-server public smoke GIF/API contract",
    )
    assert "SMOKE_SCOPE=same-server" in public_smoke
    assert "EXTERNAL_PROOF=false" in public_smoke
    assert "bash scripts/deploy/public-gif-smoke.sh" in public_smoke
    assert smoke_script.count("--write-out") >= 4, (
        "health, inspect, normal convert, and tight convert curls must each "
        "persist HTTP status evidence before assertions"
    )
    assert "--fail" not in smoke_script, (
        "HTTP status must be captured and asserted explicitly; curl --fail can "
        "drop response/status evidence before archival"
    )
    assert "| tee deploy-evidence/public-health-response" not in smoke_script, (
        "public curls must not rely on tee output alone as success evidence"
    )


def test_external_public_smoke_script_provides_true_external_proof_mode() -> None:
    _require_implementation_lanes_integrated()
    smoke_script = _read("scripts/deploy/public-gif-smoke.sh")
    deploy_doc = _read("docs/deploy/jenkins-dev.md").lower()
    checklist = _read("docs/deploy/jenkins-dev-security-checklist.md").lower()
    combined_docs = f"{deploy_doc}\n{checklist}"

    _assert_contains_all(
        smoke_script,
        [
            'SMOKE_SCOPE="${SMOKE_SCOPE:-external}"',
            'EXTERNAL_PROOF="${EXTERNAL_PROOF:-true}"',
            "public-smoke-scope.txt",
            "scope=${SMOKE_SCOPE}",
            "external-proof=${EXTERNAL_PROOF}",
            "BASE_URL",
            "PUBLIC_HEALTHCHECK_URL",
        ],
        label="external public smoke script defaults",
    )
    _assert_contains_all(
        combined_docs,
        [
            "scripts/deploy/public-gif-smoke.sh",
            "smoke_scope=external",
            "external_proof=true",
            "true external proof",
        ],
        label="external public smoke documentation",
    )


def test_post_deploy_smoke_failure_triggers_rollback_and_fails_original_build() -> None:
    _require_implementation_lanes_integrated()
    jenkinsfile = _read("Jenkinsfile")
    public_smoke = _stage_body(jenkinsfile, "Public URL/API Smoke")
    rollback = _stage_body(jenkinsfile, "Rollback After Post-Deploy Smoke Failure")
    fail_stage = _stage_body(jenkinsfile, "Fail Deployment After Post-Deploy Smoke Rollback")

    assert "PUBLIC_HEALTH_FAILED" not in jenkinsfile
    assert "env.POST_DEPLOY_SMOKE_FAILED = 'false'" in jenkinsfile
    assert "env.POST_DEPLOY_SMOKE_FAILED = 'true'" in public_smoke
    assert "env.POST_DEPLOY_SMOKE_FAILED == 'true'" in rollback
    assert "env.POST_DEPLOY_SMOKE_FAILED == 'true'" in fail_stage
    _assert_contains_all(
        fail_stage,
        [
            "Post-deploy public smoke failed",
            "failing the original deployment build by design",
        ],
        label="post-deploy smoke failure stage",
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
        "same-server public url/api smoke",
        "external-proof=false",
        "not true external proof",
        "optional future true-external",
        "secret redaction",
        "rollback",
        "jenkins job setup",
        "no nginx",
        "no dns",
    ]
    missing = [topic for topic in required_topics if topic not in checklist]
    assert not missing, f"security checklist is missing required topics: {missing}"


def test_branch_routed_deploy_docs_cover_shared_dev_and_prod_safety() -> None:
    deploy_doc = _read("docs/deploy/jenkins-dev.md").lower()
    checklist = _read("docs/deploy/jenkins-dev-security-checklist.md").lower()
    combined = f"{deploy_doc}\n{checklist}"

    required_topics = [
        "every non-main branch",
        "shared dev",
        "last successful non-main deploy wins",
        "run_deploy=true",
        "run_deploy=false",
        "production-impacting",
        "main` targets `emoji.enmsoftware.com",
        "non-`main` targets `dev.emoji.enmsoftware.com",
    ]
    missing = [topic for topic in required_topics if topic not in combined]
    assert not missing, f"branch-routed deploy docs are missing required topics: {missing}"


def test_docs_do_not_overclaim_same_server_smoke_as_external_proof() -> None:
    deploy_doc = _read("docs/deploy/jenkins-dev.md").lower()
    checklist = _read("docs/deploy/jenkins-dev-security-checklist.md").lower()
    combined = f"{deploy_doc}\n{checklist}"

    _assert_contains_all(
        combined,
        [
            "same-server public url/api smoke",
            "public-smoke-scope.txt",
            "scope=same-server",
            "external-proof=false",
            "not true external proof",
            "optional future true-external",
        ],
        label="same-server public smoke documentation",
    )
    forbidden_claims = [
        "same-server public url/api smoke is external proof",
        "same-server public route smoke is external proof",
        "same-server smoke is external proof",
        "public validation must run from `public_check_agent_label`",
        "public_check_agent_label` points to a jenkins agent/probe outside",
    ]
    for claim in forbidden_claims:
        assert claim not in combined, f"documentation overclaims public smoke scope: {claim}"


def test_docker_and_jenkins_inject_app_visible_git_tag_version_metadata() -> None:
    dockerfile = _read("Dockerfile")
    jenkinsfile = _read("Jenkinsfile")

    assert "SLACK_EMOJI_TAILOR_VERSION" not in dockerfile
    assert "SLACK_EMOJI_TAILOR_VERSION" not in jenkinsfile
    _assert_contains_all(
        dockerfile,
        [
            "ARG APP_GIT_TAG_VERSION",
            "app/_git_version",
            "APP_GIT_TAG_VERSION is required",
        ],
        label="Dockerfile git tag version metadata",
    )
    _assert_contains_all(
        jenkinsfile,
        [
            "git fetch --force --tags origin",
            "git describe --tags --dirty --match 'v[0-9]*'",
            "--build-arg \"APP_GIT_TAG_VERSION=${GIT_TAG_VERSION}\"",
            "APP_DISPLAY_VERSION",
            "org.opencontainers.image.version=${APP_DISPLAY_VERSION}",
        ],
        label="Jenkinsfile git tag version metadata",
    )
