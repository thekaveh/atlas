"""#1888: Backend SQL must qualify ``public`` objects and pgvector names.

The Backend role's search_path is ``pg_catalog, pg_temp`` (#2059). Unqualified
references to public tables, the pgvector types and functions, or the distance
operators then either fail with "does not exist" or resolve to an object a
planted overload could shadow. This static check reads every SQL string literal
in the Backend package and fails on an unqualified reference.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "services" / "backend" / "app" / "app"
SCHEMA_SCRIPTS = ROOT / "services" / "supabase" / "db" / "scripts"

PUBLIC_TABLE_DEF = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?public\.([a-z_][a-z0-9_]*)", re.IGNORECASE
)
SQL_STATEMENT = re.compile(
    r"\bSELECT\b|\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b",
    re.IGNORECASE,
)
PGVECTOR_NAME = re.compile(r"(?<![\w.])(vector|halfvec|vector_dims)\s*\(", re.IGNORECASE)
DISTANCE_OPERATOR = re.compile(r"(?<!public\.)(<=>|<->|<#>)")


def public_tables() -> set[str]:
    names: set[str] = set()
    for path in SCHEMA_SCRIPTS.glob("*"):
        if path.is_file():
            names.update(m.group(1).lower() for m in PUBLIC_TABLE_DEF.finditer(path.read_text(encoding="utf-8", errors="ignore")))
    return names


def _table_pattern(tables: set[str]) -> re.Pattern[str]:
    alternatives = "|".join(sorted(tables, key=len, reverse=True))
    return re.compile(
        rf"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:ONLY\s+)?(?!public\.)({alternatives})\b",
        re.IGNORECASE,
    )


def sql_violations(sql: str, tables: set[str]) -> list[str]:
    """Return the unqualified references found in one SQL string."""
    if not SQL_STATEMENT.search(sql):
        return []
    found = []
    for match in _table_pattern(tables).finditer(sql):
        found.append(f"unqualified public table {match.group(1)}")
    for match in PGVECTOR_NAME.finditer(sql):
        found.append(f"unqualified pgvector name {match.group(1)}")
    for match in DISTANCE_OPERATOR.finditer(sql):
        found.append(f"unqualified distance operator {match.group(1)}")
    return found


def _string_literals(source: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(p.value for p in node.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
            yield node.lineno, text


def backend_violations() -> list[str]:
    tables = public_tables()
    out = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for lineno, text in _string_literals(path.read_text(encoding="utf-8")):
            for problem in sql_violations(text, tables):
                out.append(f"{rel}:{lineno}: {problem}")
    return out


def test_public_table_list_comes_from_the_schema_scripts():
    tables = public_tables()
    assert len(tables) >= 10
    assert {"memory_facts", "memory_sessions", "media_spend_ledger", "research_sessions", "users"} <= tables


def test_backend_sql_qualifies_public_tables_and_pgvector_names():
    assert backend_violations() == []


def test_checker_flags_unqualified_table_and_pgvector_references():
    tables = {"memory_facts"}
    bad = [
        "SELECT id FROM memory_facts WHERE user_id = $1",
        "UPDATE memory_facts SET is_active = false",
        "INSERT INTO memory_facts (id) VALUES ($1)",
        "SELECT embedding <=> $1 FROM public.memory_facts",
        "SELECT vector_dims(embedding) FROM public.memory_facts",
        "SELECT $1::vector(768)",
    ]
    for sql in bad:
        assert sql_violations(sql, tables), sql


def test_checker_accepts_the_qualified_forms():
    tables = {"memory_facts"}
    good = [
        "SELECT id FROM public.memory_facts WHERE user_id = $1",
        "UPDATE public.memory_facts SET is_active = false",
        "INSERT INTO public.memory_facts (id) VALUES ($1)",
        "SELECT embedding OPERATOR(public.<=>) $1::public.vector(768) FROM public.memory_facts",
        "SELECT public.vector_dims(embedding) FROM public.memory_facts",
        "SELECT $1::public.halfvec(3072)",
        "Update the memory facts from the last session.",
    ]
    for sql in good:
        assert sql_violations(sql, tables) == [], sql
