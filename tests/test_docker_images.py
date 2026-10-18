"""API/Worker 依赖拆分与容器编排契约。"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirements_lines(path: str):
    return [
        line.strip()
        for line in (PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_api_requirements_exclude_heavy_ocr_runtimes():
    lines = _requirements_lines("requirements-api.txt")
    heavy_packages = (
        "paddlepaddle",
        "paddleocr",
        "paddlex",
        "opencv-contrib-python",
        "scipy",
    )
    for package in heavy_packages:
        assert not any(line.startswith(package) for line in lines)


def test_worker_requirements_include_heavy_ocr_runtimes():
    lines = _requirements_lines("requirements-worker.txt")
    required_packages = (
        "paddlepaddle",
        "paddleocr",
        "paddlex",
        "opencv-contrib-python",
        "scipy",
    )
    for package in required_packages:
        assert any(line.startswith(package) for line in lines)


def test_dockerfiles_use_split_requirements():
    api_dockerfile = (PROJECT_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    worker_dockerfile = (PROJECT_ROOT / "Dockerfile.worker").read_text(encoding="utf-8")

    assert "requirements-api.txt" in api_dockerfile
    assert "requirements.txt" not in api_dockerfile
    assert "requirements-worker.txt" in worker_dockerfile


def test_compose_uses_split_images_and_healthchecks():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "dockerfile: Dockerfile.api" in compose
    assert "dockerfile: Dockerfile.worker" in compose
    assert "condition: service_healthy" in compose
    assert "redis-cli" in compose
    assert "urllib.request.urlopen" in (
        PROJECT_ROOT / "Dockerfile.api"
    ).read_text(encoding="utf-8")


def test_ocr_import_path_does_not_require_opencv_at_module_load():
    for path in (
        "app/services/ocr/__init__.py",
        "app/services/ocr/preprocess.py",
        "app/services/ocr/paddle_backend.py",
    ):
        content = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        assert not any(
            line.startswith("import cv2") or line.startswith("from cv2")
            for line in content.splitlines()
        ), f"{path} imports OpenCV at module load"
