PYTHON ?= .venv/bin/python

.PHONY: db-up db-down seed analytics scenarios quality test coverage lint

db-up:
	docker compose up -d

db-down:
	docker compose down

seed:
	PYTHONPATH=src $(PYTHON) -m wbp.generate_data

analytics:
	PYTHONPATH=src $(PYTHON) -m wbp.analytics

scenarios:
	PYTHONPATH=src $(PYTHON) -m wbp.scenarios

quality:
	PYTHONPATH=src $(PYTHON) -m wbp.quality

test:
	PYTHONPATH=src $(PYTHON) -m pytest

coverage:
	PYTHONPATH=src $(PYTHON) -m pytest --cov=src/wbp --cov-report=term-missing

lint:
	PYTHONPATH=src $(PYTHON) -m ruff check src tests
