{:ok, _} = Application.ensure_all_started(:supavisor)

{:ok, version} =
  case Supavisor.Repo.query!("select version()") do
    %{rows: [[ver]]} -> Supavisor.Helpers.parse_pg_version(ver)
    _ -> nil
  end

params = %{
  "external_id" => System.fetch_env!("POOLER_TENANT_ID"),
  "db_host" => System.fetch_env!("POSTGRES_HOST"),
  "db_port" => System.fetch_env!("POSTGRES_PORT"),
  "db_database" => System.fetch_env!("POSTGRES_DB"),
  "ip_version" => "auto",
  "enforce_ssl" => false,
  "require_user" => false,
  "auth_query" => "SELECT * FROM pgbouncer.get_auth($1);",
  "default_max_clients" => System.fetch_env!("POOLER_MAX_CLIENT_CONN"),
  "default_pool_size" => System.fetch_env!("POOLER_DEFAULT_POOL_SIZE"),
  "default_parameter_status" => %{"server_version" => version},
  "users" => [
    %{
      "db_user" => System.fetch_env!("POSTGRES_USER"),
      "db_password" => System.fetch_env!("POSTGRES_PASSWORD"),
      "mode_type" => System.fetch_env!("POOLER_POOL_MODE"),
      "pool_size" => System.fetch_env!("POOLER_DEFAULT_POOL_SIZE"),
      "is_manager" => true
    }
  ]
}

case Supavisor.Tenants.get_tenant_by_external_id(params["external_id"]) do
  nil -> {:ok, _} = Supavisor.Tenants.create_tenant(params)
  _tenant -> :ok
end
