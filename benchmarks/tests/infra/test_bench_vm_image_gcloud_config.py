"""An image that holds a gcloud user config dir fails its smoke step, so it never gets a tag (#434).

The images once shipped the dir their build's gcloud wrote, with its on-GCE
cache ``gce`` reading ``False``. gcloud trusts that file for ten minutes after
its mtime, so on a VM booted within ten minutes of the image build every
gcloud call had no account.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._image_scripts import load_image_script

image_deps = load_image_script("image_deps")
smoke = load_image_script("smoke")

_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _smoke_one(check: object, capsys: pytest.CaptureFixture[str]) -> tuple[bool, dict]:
    passed = smoke.run_checks({"no_gcloud_user_config": check})
    return passed, json.loads(capsys.readouterr().out.splitlines()[0])


def test_an_image_holding_gclouds_on_gce_cache_fails_its_smoke_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "gcloud"
    config_dir.mkdir()
    (config_dir / "gce").write_text("False", encoding="utf-8")

    passed, record = _smoke_one(smoke.check_no_gcloud_user_config(config_dir), capsys)

    assert passed is False
    assert record["ok"] is False
    assert str(config_dir) in record["detail"]
    assert "'gce'" in record["detail"]


def test_an_image_with_no_gcloud_user_config_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passed, record = _smoke_one(smoke.check_no_gcloud_user_config(tmp_path / "gcloud"), capsys)

    assert passed is True
    assert record["ok"] is True


@pytest.mark.parametrize("flavour", list(image_deps.ImageFlavour))
def test_both_flavours_look_before_any_smoke_step_runs_gcloud(flavour: object) -> None:
    # The `clis` step's own `gcloud --version` can write the dir in the smoke's container.
    names = list(smoke.build_checks(flavour, _COMMIT))

    assert names.index("no_gcloud_user_config") < names.index("clis")
