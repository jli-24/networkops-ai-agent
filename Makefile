PYTHON ?= python3
NPM ?= npm

.PHONY: install test demo embedded-demo

install:
	$(PYTHON) -m pip install -e ".[dev]"
	$(NPM) --prefix console install

test:
	$(PYTHON) -m pytest -q
	$(NPM) --prefix console test

demo:
	$(PYTHON) -m demo

embedded-demo:
	$(PYTHON) -m network_agent_rag.api.embedded_check || $(PYTHON) -c "from network_agent_rag.api.embedded import EmbeddedServices; s=EmbeddedServices(artifact_root='data/artifacts-demo'); print('EmbeddedOps services ready:', s.capability_registry.list())"
