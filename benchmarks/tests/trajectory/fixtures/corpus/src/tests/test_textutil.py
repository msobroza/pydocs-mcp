"""Tests for widgetlib.textutil."""

import pytest

from widgetlib.textutil import slugify, truncate


def test_slugify_already_lowercase():
    assert slugify("simple case") == "simple-case"


def test_slugify_strips_symbols():
    assert slugify("a!!b") == "a-b"


def test_slugify_mixed_case():
    assert slugify("Hello World") == "hello-world"


def test_truncate_short():
    assert truncate("hi", 5) == "hi"


def test_truncate_long():
    assert truncate("hello world", 5) == "hello…"


def test_truncate_negative_raises():
    with pytest.raises(ValueError):
        truncate("x", -1)
