# The built-in providers register through entry points in the package
# metadata, so a checkout must be installed before anything discovers them.
# PYTHON must be the interpreter clew runs under: make PYTHON=... if it is
# not the first python3 on PATH.
PYTHON ?= python3

dev:
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m unittest discover -s tests

.PHONY: dev test
