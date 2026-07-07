from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.exists(), f"Missing required deploy artifact: {relative_path}"
    return path.read_text(encoding="utf-8")


def test_deploy_cleanup_is_project_scoped_and_preserves_rollback_images() -> None:
    deploy_script = _read("scripts/deploy/jenkins-enm-deploy.sh")
    rollback_script = _read("scripts/deploy/jenkins-enm-rollback.sh")
    jenkinsfile = _read("Jenkinsfile")
    deploy_doc = _read("docs/deploy/jenkins-dev.md").lower()
    checklist = _read("docs/deploy/jenkins-dev-security-checklist.md").lower()
    combined_scripts = f"{deploy_script}\n{rollback_script}"
    combined_docs = f"{deploy_doc}\n{checklist}"

    forbidden_cleanup = [
        "docker image prune",
        "docker system prune",
        "docker builder prune",
        "docker image rm -f",
        "docker rmi -f",
    ]
    for forbidden in forbidden_cleanup:
        assert forbidden not in combined_scripts, (
            f"deploy cleanup must stay project-scoped and conservative, not use {forbidden!r}"
        )

    required_jenkins_policy = [
        "DEPLOY_IMAGE_CLEANUP_ENABLED",
        "DEPLOY_IMAGE_RETENTION_COUNT",
        "Minimum recent same-environment image tags",
    ]
    missing_jenkins_policy = [
        item for item in required_jenkins_policy if item not in jenkinsfile
    ]
    assert not missing_jenkins_policy, (
        f"Jenkinsfile is missing image cleanup policy controls: {missing_jenkins_policy}"
    )

    required_deploy_policy = [
        "cleanup_project_images",
        "image_cleanup_tag_prefix",
        "docker image ls",
        'docker image rm "$candidate_ref"',
        "docker ps -q",
        "current-image-ref",
        "previous-image-ref",
        "DEPLOY_IMAGE_RETENTION_COUNT",
        "image-cleanup.txt",
    ]
    missing_deploy_policy = [
        item for item in required_deploy_policy if item not in deploy_script
    ]
    assert not missing_deploy_policy, (
        f"deploy script is missing conservative image cleanup policy: {missing_deploy_policy}"
    )

    assert deploy_script.index("cleanup_project_images") < deploy_script.rindex(
        "cleanup_project_images"
    )
    assert deploy_script.index("printf '%s\\n' \"$image_ref\" >\"$marker_dir/current-image-ref\"") < deploy_script.rindex(
        "cleanup_project_images"
    ), "image cleanup must run only after the new deployment is marked current"

    required_docs = [
        "project-scoped image cleanup",
        "same-environment",
        "current-image-ref",
        "previous-image-ref",
        "running container",
        "recent image tags",
        "must not use `docker image prune`",
    ]
    missing_docs = [item for item in required_docs if item not in combined_docs]
    assert not missing_docs, f"deploy docs are missing cleanup policy: {missing_docs}"
