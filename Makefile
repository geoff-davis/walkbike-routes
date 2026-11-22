.PHONY: format lint typecheck test check hooks demo validation-check
format: ## apply safe lint fixes and format
	uv run --locked ruff check --fix src tests scripts
	uv run --locked ruff format src tests scripts
lint: ## lint and check formatting
	uv run --locked ruff check src tests scripts
	uv run --locked ruff format --check src tests scripts
typecheck: ## run mypy
	uv run --locked mypy
test: ## run the tests with coverage
	uv run --locked pytest -q --cov
check: lint typecheck test ## all local quality gates
hooks: ## install the git hooks
	uv run --locked pre-commit install
demo: ## run the made-up demo town end to end (no downloads)
	uv run --locked walkbike-routes run --config config/demo.yaml --out output/demo
validation-check: ## lint, type-check and test the separate validation folder (not part of check)
	uv run --locked ruff check validation
	uv run --locked ruff format --check validation
	MYPYPATH=src:validation uv run --locked mypy validation/wbvalidate validation/tests validation/run.py
	uv run --locked pytest -q validation/tests
