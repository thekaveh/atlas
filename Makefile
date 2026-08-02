PYTHON ?= uv run --project bootstrapper python
MKDOCS ?= NO_MKDOCS_2_WARNING=1 uv run --project bootstrapper mkdocs

.PHONY: help docs-build docs-check docs-serve docs-wiki

help:
	@printf '%s\n' \
	  'docs-build  Build the generated MkDocs input and strict site output' \
	  'docs-check  Validate all three documentation surfaces' \
	  'docs-serve  Build and serve the documentation locally' \
	  'docs-wiki   Build and validate the GitHub wiki export'

docs-build:
	$(PYTHON) -m scripts.docs.canonical_references
	$(PYTHON) -m scripts.docs.build_docs --site
	$(MKDOCS) build --strict

docs-check:
	$(PYTHON) -m scripts.docs.build_docs --verify-diagrams
	$(PYTHON) -m scripts.docs.check_docs
	$(MKDOCS) build --strict
	$(PYTHON) -m scripts.docs.check_site --built-only
	$(PYTHON) -m scripts.docs.push_wiki --check

docs-serve:
	$(PYTHON) -m scripts.docs.build_docs --site
	$(MKDOCS) serve

docs-wiki:
	$(PYTHON) -m scripts.docs.build_docs --wiki
	$(PYTHON) -m scripts.docs.push_wiki --check
