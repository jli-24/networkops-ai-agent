PYTHON ?= python3
NPM ?= npm

.PHONY: install test demo embedded-demo verify-fresh

install:
	$(PYTHON) -m pip install -e ".[dev]"
	$(NPM) --prefix console install

test:
	$(PYTHON) -m pytest -q
	$(NPM) --prefix console test

demo:
	$(PYTHON) -m demo

embedded-demo:
	$(PYTHON) -c "from network_agent_rag.packs.embeddedops.api import EmbeddedServices; s=EmbeddedServices(artifact_root='data/artifacts-demo'); print('EmbeddedOps pack ready:', [c.key for c in s.capability_registry.list()])"

verify-fresh:
	$(PYTHON) scripts/verify_fresh.py
