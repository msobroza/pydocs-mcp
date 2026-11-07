.PHONY: install test test-rust lint lint-rust format typecheck gate build clean docs-serve docs-build \
	bench-vm bench-vm-status bench-vm-stop bench-vm-pull bench-vm-check

install:
	pip install -e .
	pip install --group dev
	maturin develop --release

test:
	python -m pytest tests/ -v --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-report=term-missing

test-rust:
	cargo test

lint:
	ruff check python/ tests/ benchmarks/ scripts/
	ruff format --check python/ tests/ benchmarks/ scripts/

lint-rust:
	cargo fmt --check
	cargo clippy -- -D warnings

format:
	ruff check --fix python/ tests/ benchmarks/ scripts/
	ruff format python/ tests/ benchmarks/ scripts/
	cargo fmt

typecheck:
	mypy python/pydocs_mcp

# Full local quality gate over the calibrated surface (python/pydocs_mcp +
# tests): lint + format + types + cognitive complexity (snapshot-baselined,
# ceiling 15) + dead code + the 90%-line-coverage test run. Run before pushing.
gate:
	ruff check python/ tests/ benchmarks/ scripts/
	ruff format --check python/ tests/ benchmarks/ scripts/
	mypy python/pydocs_mcp
	complexipy python/pydocs_mcp --max-complexity-allowed 15
	vulture python/pydocs_mcp --min-confidence 80
	python -m pytest tests/ --ignore=tests/test_parity.py \
		--cov=pydocs_mcp --cov-report=term-missing --cov-fail-under=90

build:
	maturin build --release

# Bench VM (ADR 0026): launch the first attempt of a run spec on an ephemeral
# GCP VM and return without waiting. RUN is relative to infra/bench-vm/.
# Runs as your `gcloud auth login` session; names come from `terraform output`.
# DRY_RUN=1 runs the laptop-only checks and prints the plan: no gcloud, no
# terraform, nothing written.
#   make bench-vm RUN=runs/cpu-smoke.yaml [DRY_RUN=1]
bench-vm:
	@test -n "$(RUN)" || { echo "usage: make bench-vm RUN=runs/<name>.yaml [DRY_RUN=1]" >&2; exit 2; }
	PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm launch infra/bench-vm/$(RUN) $(if $(filter-out 0,$(DRY_RUN)),--dry-run)

# See, stop and bring home a run, named by its run spec like `make bench-vm`.
# status: the run's status history, commits, image, machine and log tail; with
#   no RUN, every live bench VM. stop: delete the run's live VM; its supervisor
#   records `stopped` and `make bench-vm` relaunches it later. pull: download
#   gs://<runs-bucket>/<run-id>/ into benchmarks/results/bench-vm/<run-id>/
#   (git-ignored), where the reporting tools read it.
#   make bench-vm-status [RUN=runs/cpu-smoke.yaml]
#   make bench-vm-stop RUN=runs/cpu-smoke.yaml
#   make bench-vm-pull RUN=runs/cpu-smoke.yaml
bench-vm-status:
	PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm status $(if $(RUN),infra/bench-vm/$(RUN))

bench-vm-stop:
	@test -n "$(RUN)" || { echo "usage: make bench-vm-stop RUN=runs/<name>.yaml" >&2; exit 2; }
	PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm stop infra/bench-vm/$(RUN)

bench-vm-pull:
	@test -n "$(RUN)" || { echo "usage: make bench-vm-pull RUN=runs/<name>.yaml" >&2; exit 2; }
	PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm pull infra/bench-vm/$(RUN)

# Check the bench VM tree with no credentials and no Terraform backend: every
# committed run spec, shellcheck on its scripts, hadolint on its Dockerfiles,
# terraform fmt -check, and terraform validate plus the mocked terraform test
# after an init into a throwaway data dir. CI runs it on every infra/** change.
#   make bench-vm-check
bench-vm-check:
	bash infra/bench-vm/check.sh

# Docs site (Sphinx + Furo + MyST). --no-install-project skips the maturin/Rust
# build; conf.py reads pydocs_mcp from python/ on sys.path for autodoc.
docs-serve:
	uv sync --group docs --no-install-project
	uv run sphinx-autobuild documentation documentation/_build/html

docs-build:
	uv sync --group docs --no-install-project
	uv run sphinx-build -W --keep-going -b html documentation documentation/_build/html

clean:
	rm -rf build/ dist/ target/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
