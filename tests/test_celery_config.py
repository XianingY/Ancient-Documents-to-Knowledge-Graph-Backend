import os
import subprocess
import sys


def test_celery_env_does_not_override_container_redis_port(tmp_path):
    (tmp_path / ".env").write_text(
        "SECRET_KEY=dotenv-secret\nREDIS_HOST=redis\nREDIS_PORT=6380\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.getcwd(),
            "SECRET_KEY": "env-secret",
            "REDIS_HOST": "redis",
            "REDIS_PORT": "6379",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.celery_app import celery_app; print(celery_app.conf.broker_url)",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "redis://redis:6379/0"
