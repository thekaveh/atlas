PYTHON ?= uv run --project bootstrapper python
MKDOCS ?= uv run --project bootstrapper mkdocs
BOUNDED ?= python3 -m scripts.bounded_subprocess

.PHONY: help docs-build docs-check docs-serve docs-wiki

help:
	@printf '%s\n' \
	  'docs-build  Build the generated MkDocs input and strict site output' \
	  'docs-check  Validate all three documentation surfaces' \
	  'docs-serve  Build and serve the documentation locally' \
	  'docs-wiki   Build and validate the GitHub wiki export'

docs-build:
	$(BOUNDED) --label "canonical docs references" --forward-stderr -- $(PYTHON) -m scripts.docs.canonical_references
	$(BOUNDED) --label "three-surface site generation" --forward-stderr -- $(PYTHON) -m scripts.docs.build_docs --site
	$(BOUNDED) --label "strict MkDocs build" --forward-stderr -- env NO_MKDOCS_2_WARNING=1 $(MKDOCS) build --strict

docs-check:
	$(BOUNDED) --label "diagram verification" --forward-stderr -- $(PYTHON) -m scripts.docs.build_docs --verify-diagrams
	$(BOUNDED) --label "three-surface docs contracts" --forward-stderr -- $(PYTHON) -m scripts.docs.check_docs
	$(BOUNDED) --label "strict MkDocs build" --forward-stderr -- env NO_MKDOCS_2_WARNING=1 $(MKDOCS) build --strict
	$(BOUNDED) --label "built-site link check" --forward-stderr -- $(PYTHON) -m scripts.docs.check_site --built-only
	$(BOUNDED) --label "wiki dry run" --forward-stderr -- $(PYTHON) -m scripts.docs.push_wiki --check

docs-serve:
	$(BOUNDED) --label "three-surface site generation" --forward-stderr -- $(PYTHON) -m scripts.docs.build_docs --site
	NO_MKDOCS_2_WARNING=1 $(MKDOCS) serve

docs-wiki:
	$(BOUNDED) --label "three-surface wiki generation" --forward-stderr -- $(PYTHON) -m scripts.docs.build_docs --wiki
	$(BOUNDED) --label "wiki dry run" --forward-stderr -- $(PYTHON) -m scripts.docs.push_wiki --check
