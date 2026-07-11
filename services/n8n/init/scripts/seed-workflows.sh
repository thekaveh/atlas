#!/bin/sh
# seed-workflows.sh — Atlas consumer n8n workflow seeding (#412), thin wrapper.
#
# Runs in the Atlas-owned ``n8n-seed`` container (the n8n image). All of the real
# seeding logic — importing each consumer workflow, activating it via the n8n
# public API, reconciling removed workflows, and probing declared webhooks — lives
# in the sibling ``seed-workflows.js`` and runs under node.
#
# Why node instead of shell: the n8n image is Alpine/BusyBox and its ``wget`` has
# no ``--method`` option (and there is no curl), so a shell ``wget --method=POST``
# for the activation/reconcile/probe API calls silently fails on every request.
# node is always present in the n8n image and does reliable HTTP + the n8n CLI.
#
# Best-effort: the node seeder isolates per-workflow failures and always exits 0,
# so one bad consumer workflow can never abort a ``docker compose up --wait``.
set -eu
exec node /scripts/seed-workflows.js
