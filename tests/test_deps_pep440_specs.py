"""Regression tests for normalize_package_name against PEP 440 spec forms
that the original split-class regex mishandled.

Gap: 'requests~=2.31' (compatible-release operator) split on '=' but kept
the preceding '~', producing 'requests~' — a name that matches no installed
distribution, so the dependency silently drops out of indexing. Direct-URL
refs ('pkg@git+https://...', no surrounding whitespace) had no split
character before '@' at all, so the whole spec passed through unmangled.
"""

from pydocs_mcp.deps import normalize_package_name


class TestNormalizeCompatibleRelease:
    """PEP 440 '~=' compatible-release specifiers."""

    def test_strips_compatible_release_operator(self):
        assert normalize_package_name("requests~=2.31") == "requests"

    def test_strips_compatible_release_operator_hyphenated_name(self):
        assert normalize_package_name("scikit-learn~=1.4") == "scikit_learn"


class TestNormalizeDirectUrlRef:
    """PEP 508 direct references ('name @ url'), with and without whitespace."""

    def test_strips_url_ref_with_spaces(self):
        assert normalize_package_name("pkg @ git+https://example.com/pkg.git") == "pkg"

    def test_strips_url_ref_without_spaces(self):
        assert normalize_package_name("pkg@git+https://example.com/pkg.git") == "pkg"

    def test_spaced_and_unspaced_url_refs_agree(self):
        spaced = normalize_package_name("pkg @ git+https://example.com/pkg.git")
        unspaced = normalize_package_name("pkg@git+https://example.com/pkg.git")
        assert spaced == unspaced == "pkg"
