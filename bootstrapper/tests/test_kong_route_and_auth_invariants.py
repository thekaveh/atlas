"""Two invariants that a route/consumer inventory cannot express by counting.

Both defects below shipped, and both were verified against the pinned
`kong:3.9.3` image before and after the fix.
"""

from __future__ import annotations

import re

import pytest
import yaml

from core.config_parser import ConfigParser
from utils.kong_config_generator import KongConfigGenerator

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def kong_config(tmp_path_factory) -> dict:
    """Generate against a `.env` that HAS the Supabase keys.

    They must be written to the file, not poked into `generator.env_vars`:
    `generate_kong_config()` calls `load_environment_variables()` itself, which
    re-reads `.env` and discards any post-hoc mutation. An earlier version of
    this fixture did exactly that and passed only on a developer machine whose
    `.env` had been through key generation — in CI, where `.env` is
    materialized from `.env.example` with the keys blank, it failed. A test
    that depends on the author's local secrets is not a test.
    """
    env_path = tmp_path_factory.mktemp("kong") / ".env"
    source = (REPO_ROOT / ".env").read_text(encoding="utf-8") if (REPO_ROOT / ".env").exists() \
        else (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    lines = [
        line for line in source.splitlines()
        if not line.startswith(("SUPABASE_ANON_KEY=", "SUPABASE_SERVICE_KEY="))
    ]
    lines += ["SUPABASE_ANON_KEY=test-anon-key", "SUPABASE_SERVICE_KEY=test-service-key"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = env_path
    generator = KongConfigGenerator(parser)
    raw = generator.generate_kong_config()
    return yaml.safe_load(raw) if isinstance(raw, str) else raw


def _plugin_names(holder: dict) -> set:
    return {p.get("name") for p in (holder.get("plugins") or [])}


def _key_auth_services(config: dict, *, include_routes: bool = False) -> set:
    """Service names whose requests must present an api key."""
    found = set()
    for service in config["services"]:
        guarded = "key-auth" in _plugin_names(service)
        if include_routes and not guarded:
            guarded = any(
                "key-auth" in _plugin_names(route)
                for route in service.get("routes") or []
            )
        if guarded:
            found.add(service["name"])
    return found


def _key_credentials(config: dict) -> list:
    return [
        cred
        for consumer in (config.get("consumers") or [])
        for cred in consumer.get("keyauth_credentials", [])
    ]


def test_no_key_auth_plugin_is_unsatisfiable(kong_config):
    """A `key-auth` plugin with no matching credential rejects EVERYTHING.

    Five Supabase services enforce `key-auth` with no `anonymous`
    fallthrough, and DB-less Kong has no admin API — the declarative file is
    the only credential source. With zero `keyauth_credentials`, a request
    carrying the correct SUPABASE_ANON_KEY was `401 Unauthorized`,
    indistinguishable from a wrong key. Verified against kong:3.9.3.

    Asserting the INVARIANT rather than a consumer count: the previous test
    asserted `len(consumers) == 1`, which pinned the broken state as correct.
    """
    guarded = sorted(_key_auth_services(kong_config, include_routes=True))
    credentials = _key_credentials(kong_config)
    assert not guarded or credentials, (
        f"these services enforce key-auth but NO consumer carries a key, so "
        f"every request to them returns 401: {guarded}"
    )
    assert all(c.get("key") for c in credentials), "an empty key authenticates nobody"


def test_supabase_key_auth_services_are_all_covered(kong_config):
    """Pin the specific surface, so a silent regression is loud."""
    assert {"auth-v1", "rest-v1", "storage-v1"} <= _key_auth_services(kong_config)
    usernames = {c["username"] for c in kong_config["consumers"]}
    assert {"anon", "service_role"} <= usernames


def _matches(path_pattern: str, candidate: str) -> bool:
    """Kong's own matching rules for a declarative path.

    A path containing regex metacharacters IS the regex, matched anchored at
    the start; otherwise it is a plain prefix.
    """
    if re.search(r"[$^*+?()\[\]{}|\\]", path_pattern):
        return re.match(path_pattern, candidate) is not None
    return candidate.startswith(path_pattern)


def _route_paths(service: dict) -> list:
    """(path, hosts) pairs for one service."""
    return [
        (path, frozenset(route.get("hosts") or []))
        for route in service.get("routes") or []
        for path in route.get("paths") or []
    ]


def _all_route_paths(config: dict) -> list:
    return [
        (service["name"], path, hosts)
        for service in config["services"]
        for path, hosts in _route_paths(service)
    ]


def _hosts_overlap(left: frozenset, right: frozenset) -> bool:
    """A route with NO hosts matches every host, so it overlaps everything."""
    return not left or not right or bool(left & right)


def _shadowed_by(patterns: list, others: list) -> list:
    shadowed = []
    for name, path, hosts in others:
        for pattern, dash_hosts in patterns:
            if _hosts_overlap(hosts, dash_hosts) and _matches(pattern, path):
                shadowed.append((name, path))
    return shadowed


def test_the_root_dashboard_route_does_not_shadow_any_api_route(kong_config):
    """It matched host + path, which OUTRANKS a path-only route in Kong.

    Match weight, not prefix length, decides: `paths: ['/'] + hosts:
    ['localhost']` is two criteria and beat every Supabase route's single
    path criterion no matter how much longer its prefix. Every /rest/v1/,
    /auth/v1/, /storage/v1/, /graphql/v1/ and /pg/ request to host
    `localhost` was answered `200` with the dashboard's HTML instead of being
    proxied — worse than a 404, because clients saw a success status.
    """
    dashboard = next(
        s for s in kong_config["services"] if s["name"] == "atlas-root-dashboard"
    )
    dash_routes = _route_paths(dashboard)
    assert dash_routes, "the dashboard route declares no path"

    others = [
        row for row in _all_route_paths(kong_config)
        if row[0] != "atlas-root-dashboard"
    ]
    assert others, "precondition: there are other path routes to shadow"

    shadowed = _shadowed_by(dash_routes, others)
    assert not shadowed, (
        f"the root dashboard routes {dash_routes} also match these API paths, "
        f"and outranks them on match weight: {shadowed}"
    )


def test_the_dashboard_still_serves_the_bare_root(kong_config):
    """Narrowing the path must not take the dashboard itself offline."""
    dashboard = next(
        s for s in kong_config["services"] if s["name"] == "atlas-root-dashboard"
    )
    assert any(_matches(pattern, "/") for pattern, _ in _route_paths(dashboard))
