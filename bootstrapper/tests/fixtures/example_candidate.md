---
slug: example-candidate
name: Example Candidate
type: external-service
category-fit: agents
generated: 2026-05-18
upstream: https://github.com/example/example-candidate
license: MIT
lifecycle: proposed
referenced-by: [example-service]
---

# Example Candidate

## Headline
A synthetic example used by the Phase B validator + merge test fixtures.

## Problem it solves
Demonstrates the candidate one-pager schema. Has no real-world use; exists
only to anchor automated tests against a known-valid shape.

## Stack wiring sketch
- example-service → example-candidate via HTTP on port 8080
- example-candidate → hermes via the same MCP protocol Hermes already speaks

## Effort
small — single compose fragment, no build context.

## Risks & open questions
- Upstream license terms may change.

## Why now (and why not sooner)
Optional section, kept here to verify the validator tolerates its presence.

## Upstream evidence
See https://github.com/example/example-candidate/releases for the most
recent stable release.
