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
