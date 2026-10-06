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
    assert "install_backend_user_sync()" in source


def test_open_webui_sync_skips_gotrue_identities_before_insert() -> None:
    # The public.users RLS policy rejects an INSERT for a GoTrue-owned id
    # before ON CONFLICT ... WHERE can skip it (verified on supabase/postgres),
    # so both the trigger and the backfill must exclude those ids up front.
    script = SCRIPT.read_text(encoding="utf-8")
    assert "IF EXISTS (SELECT 1 FROM auth.users WHERE id = candidate_id::uuid) THEN\n            RETURN NEW;" in script
    assert 'AND NOT EXISTS (SELECT 1 FROM auth.users a WHERE a.id::text = lower("user".id))' in script
