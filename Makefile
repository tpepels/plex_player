.PHONY: test lint run

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

test:
	$(PYTHON) -m unittest -q

lint:
	$(PYTHON) -m compileall -q core services tests plexlcd.py

run:
	$(PYTHON) plexlcd.py
