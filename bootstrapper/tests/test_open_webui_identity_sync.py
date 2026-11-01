import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "services/open-webui/init/scripts/register-tools.py"


def test_open_webui_users_are_synchronized_to_backend_owners() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def install_backend_user_sync" in source
    assert "CREATE OR REPLACE FUNCTION public.handle_open_webui_user_sync" in source
    assert 'ON public."user"' in source
    assert "AFTER INSERT OR DELETE OR UPDATE OF name" in source
    assert "INSERT INTO public.users" in source
    assert "NOT EXISTS (" in source and "FROM auth.users" in source


def _main_call_lines(source: str) -> dict[str, int]:
    tree = ast.parse(source, filename=str(SCRIPT))
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    lines: dict[str, int] = {}
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            lines[name] = min(lines.get(name, node.lineno), node.lineno)
    return lines


def test_main_installs_the_user_sync_before_the_admin_lookup() -> None:
    # A substring check also matches the `def` line, so it cannot tell
    # whether main() still calls the installer. The admin lookup can exit
    # early, so the trigger must be installed before it.
    calls = _main_call_lines(SCRIPT.read_text(encoding="utf-8"))
    assert "install_backend_user_sync" in calls
    assert "get_admin_user_id" in calls
    assert calls["install_backend_user_sync"] < calls["get_admin_user_id"]


def test_open_webui_sync_skips_gotrue_identities_before_insert() -> None:
    # The public.users RLS policy rejects an INSERT for a GoTrue-owned id
    # before ON CONFLICT ... WHERE can skip it (verified on supabase/postgres),
    # so both the trigger and the backfill must exclude those ids up front.
    script = SCRIPT.read_text(encoding="utf-8")
    assert "IF EXISTS (SELECT 1 FROM auth.users WHERE id = candidate_id::uuid) THEN\n            RETURN NEW;" in script
    assert 'AND NOT EXISTS (SELECT 1 FROM auth.users a WHERE a.id::text = lower("user".id))' in script
