"""No bench VM test can see a provider key from the developer's environment (spec AC-R21).

An autouse fixture strips every provider-key variable before each test, so a
real key can reach no child, log or assertion message. The checks below name
the variables they find, never their values.
"""

from __future__ import annotations

import os

from pydocs_eval.bench_vm.provider_secrets import PROVIDER_KEYS


def _provider_key_variables_present() -> list[str]:
    return sorted(name for name in os.environ if name in PROVIDER_KEYS or name.endswith("_API_KEY"))


def test_no_declared_provider_key_variable_reaches_a_test() -> None:
    assert [name for name in _provider_key_variables_present() if name in PROVIDER_KEYS] == []


def test_no_other_api_key_variable_reaches_a_test() -> None:
    assert _provider_key_variables_present() == []
