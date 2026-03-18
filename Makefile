.PHONY: test lint run

PYTHON ?= python3

test:
	$(PYTHON) -m unittest -q

lint:
	$(PYTHON) -m compileall -q core services tests plexlcd.py

run:
	$(PYTHON) plexlcd.py
