"""AC-15: README documents libopenblas-pthread-dev as a Linux system requirement."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_libopenblas() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "libopenblas-pthread-dev" in readme, (
        "README must document the libopenblas-pthread-dev system requirement"
    )


def test_install_md_exists() -> None:
    assert (ROOT / "INSTALL.md").exists(), "INSTALL.md must exist"


def test_install_md_mentions_libopenblas() -> None:
    install = (ROOT / "INSTALL.md").read_text()
    assert "libopenblas-pthread-dev" in install
    assert "apt-get" in install or "apt install" in install
