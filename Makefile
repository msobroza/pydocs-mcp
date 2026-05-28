.PHONY: install test test-rust lint lint-rust format typecheck build clean

install:
	pip install -e .
	pip install --group dev
	maturin develop --release

test:
	python -m pytest tests/ -v --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-report=term-missing

test-rust:
	cargo test

lint:
	ruff check python/ tests/ benchmarks/
	ruff format --check python/ tests/ benchmarks/

lint-rust:
	cargo fmt --check
	cargo clippy -- -D warnings

format:
	ruff check --fix python/ tests/ benchmarks/
	ruff format python/ tests/ benchmarks/
	cargo fmt

typecheck:
	mypy python/pydocs_mcp

build:
	maturin build --release

clean:
	rm -rf build/ dist/ target/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
