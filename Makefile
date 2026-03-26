.PHONY: test lint run

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m unittest -q

lint:
	$(PYTHON) -m compileall -q core services tests plexlcd.py

run:
	$(PYTHON) plexlcd.py
