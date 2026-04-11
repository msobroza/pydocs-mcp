"""Tests for fake_project generator."""
import tempfile
from pathlib import Path
from benchmarks.fake_project import generate_fake_project, FAKE_REQUIREMENTS


def test_generate_creates_py_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "myproject"
        generate_fake_project(root)
        py_files = list(root.rglob("*.py"))
        assert len(py_files) >= 3


def test_generate_creates_requirements():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "myproject"
        generate_fake_project(root)
        req = root / "requirements.txt"
        assert req.exists()
        content = req.read_text()
        assert "requests" in content


def test_fake_requirements_list():
    assert "requests" in FAKE_REQUIREMENTS
    assert "pandas" in FAKE_REQUIREMENTS
    assert len(FAKE_REQUIREMENTS) >= 3
