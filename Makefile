.PHONY: help hooks format format-all lint typecheck test check audit demo \
	validation-lint validation-typecheck validation-test validation-check
.DEFAULT_GOAL := help

PY_PATHS := src tests scripts
PIP_AUDIT := pip-audit@2.10.0

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'

hooks:  ## Install git hooks (once per clone)
	uv run --locked prek install

# Scoped on purpose: formatting touches only the files you name, so unrelated
# work in the tree is not rewritten.
format:  ## Fix and format the given files: make format FILES="src/a.py tests/test_a.py"
	@test -n "$(FILES)" || { echo 'usage: make format FILES="path ..."  (whole tree: make format-all)'; exit 2; }
	uv run --locked ruff check --fix $(FILES)
	uv run --locked ruff format $(FILES)

format-all:  ## Maintenance: fix and format the whole tree (commit it separately)
	uv run --locked ruff check --fix $(PY_PATHS) validation
	uv run --locked ruff format $(PY_PATHS) validation

lint:  ## Lint and check formatting without modifying files
	uv run --locked ruff check $(PY_PATHS)
	uv run --locked ruff format --check $(PY_PATHS)

typecheck:  ## Run mypy on the scope configured in pyproject.toml
	uv run --locked mypy

test:  ## Run the tests with coverage
	uv run --locked pytest -q --cov

# Separate recursive calls keep this order under `make -j`: a failed cheap
# check stops before the tests start.
check:  ## Definition of done: lint, types, tests
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test

audit:  ## Check every locked dependency for known vulnerabilities
	@req=$$(mktemp) && trap 'rm -f "$$req"' EXIT && \
	uv export --frozen --all-groups --no-emit-project --quiet -o "$$req" && \
	uvx $(PIP_AUDIT) --strict --disable-pip --no-deps -r "$$req"

demo:  ## Run the made-up demo town end to end (no downloads)
	uv run --locked walkbike-routes run --config config/demo.yaml --out output/demo

# validation/ is a separate experiment, not part of `check`; CI runs it too.
validation-lint:  ## Lint and check formatting of the validation folder
	uv run --locked ruff check validation
	uv run --locked ruff format --check validation

validation-typecheck:  ## Type-check the validation folder against the package
	MYPYPATH=src:validation uv run --locked mypy validation/wbvalidate validation/tests validation/run.py

validation-test:  ## Test the validation folder
	uv run --locked pytest -q validation/tests

validation-check:  ## Lint, type-check and test the validation folder
	$(MAKE) validation-lint
	$(MAKE) validation-typecheck
	$(MAKE) validation-test
