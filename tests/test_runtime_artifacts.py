import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_built_wheel_contains_chat_asset(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / "app", source / "app", ignore=shutil.ignore_patterns("__pycache__"))
    wheels = tmp_path / "wheels"
    subprocess.run([
        sys.executable, "-m", "pip", "--isolated", "wheel", "--no-index",
        "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheels), str(source),
    ], check=True, capture_output=True, text=True)
    with ZipFile(next(wheels.glob("*.whl"))) as wheel:
        assert wheel.read("app/static/chat.html") == (ROOT / "app/static/chat.html").read_bytes()


@pytest.mark.parametrize("migration_status", [0, 19])
def test_runtime_start_requires_successful_migrations(tmp_path, migration_status):
    command = tomllib.loads((ROOT / "railway.toml").read_text())["deploy"]["startCommand"]
    programs = tmp_path / "bin"
    programs.mkdir()
    marker = tmp_path / "gunicorn-started"
    python = programs / "python"
    python.write_text(f"#!/bin/sh\nexit {migration_status}\n")
    gunicorn = programs / "gunicorn"
    gunicorn.write_text(f"#!/bin/sh\nprintf '%s' started > '{marker}'\n")
    python.chmod(0o755)
    gunicorn.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-c", command], env={"PATH": str(programs), "PORT": "8000"},
        capture_output=True, text=True,
    )
    assert result.returncode == migration_status
    assert marker.exists() is (migration_status == 0)
