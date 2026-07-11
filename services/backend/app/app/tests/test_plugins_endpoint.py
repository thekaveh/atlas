"""GET /plugins inventory endpoint — #402.

Exercised through the full app (fastapi_client), so it runs in the CI backend
venv that installs main.py's import closure.
"""


def test_plugins_endpoint_returns_inventory_list(fastapi_client):
    resp = fastapi_client.get("/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert "plugins" in body
    assert isinstance(body["plugins"], list)
