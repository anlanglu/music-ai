# Developer shortcuts. Everything runs through the project's own virtual environment.
PY := .venv/bin/python

.PHONY: test test-full app clean

test:        ## fast test suite (no model downloads needed): about 3 seconds
	$(PY) -m pytest tests -q

test-full:   ## also sweep the theory composer over ~140,000 settings combinations (about 15 seconds)
	$(PY) -m pytest tests -q --full

app:         ## build Composer.app
	zsh MusicAI/build_app.sh

clean:       ## remove caches and build products (never touches output/)
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +
	rm -rf .pytest_cache MusicAI/.build
