"""依赖与测试入口的工程化契约。"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_scipy_is_declared_as_runtime_dependency():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert any(
        line.startswith("scipy==")
        for line in requirements.splitlines()
        if not line.lstrip().startswith("#")
    )


def test_default_pytest_run_excludes_live_api_tests():
    pytest_ini = PROJECT_ROOT / "pytest.ini"
    assert pytest_ini.exists()
    content = pytest_ini.read_text(encoding="utf-8")
    assert "testpaths = tests" in content
    assert 'addopts = -m "not live_api"' in content
