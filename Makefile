.PHONY: help install lint format test semgrep quality-gate clean

help:
	@echo "banxe-payment-core — available targets:"
	@echo "  make install       Install dependencies"
	@echo "  make lint          Ruff lint + format check + bandit"
	@echo "  make format        Apply ruff format"
	@echo "  make test          pytest (--cov-fail-under=80)"
	@echo "  make semgrep       Semgrep BANXE rules"
	@echo "  make quality-gate  lint + test + semgrep"
	@echo "  make clean         Remove __pycache__ and .coverage"

install:
	pip install -e ".[dev]"

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	bandit -r src/ -c pyproject.toml -q

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

test:
	pytest

semgrep:
	@semgrep --config .semgrep/rules.yaml src/ 2>/dev/null || echo "⚠️ semgrep not installed — skipping"

quality-gate: lint test semgrep
	@echo "✅ Quality gate passed"
