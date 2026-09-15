# The engine and its providers are separate distributions. Install them all
# editable to work on the checkout; the tests need every provider present.
# PYTHON must be the interpreter clew runs under: make PYTHON=... if it is
# not the first python3 on PATH.
PYTHON ?= python3
PROVIDERS := $(wildcard providers/*)

dev:
	$(PYTHON) -m pip install -e . $(addprefix -e ,$(PROVIDERS))

# The engine's suite, then each provider's, from its own tests/.
test:
	$(PYTHON) -m unittest discover -s tests
	@for p in $(PROVIDERS); do echo "== $$p"; $(PYTHON) -m unittest discover -s $$p/tests || exit 1; done

.PHONY: dev test
