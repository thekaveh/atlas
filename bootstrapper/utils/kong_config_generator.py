"""
Dynamic Kong Configuration Generator

Generates Kong API Gateway configuration based on SOURCE values from environment.
Replaces static kong.yml/kong-local.yml with dynamic service routing.
"""

import yaml
import socket
from typing import Dict, Any, List, Optional
from pathlib import Path
from urllib.parse import urlparse

from core.config_parser import DEFAULT_BASE_PORT


def _lua_long_string(value: str) -> str:
    """Embed text in a Lua long-bracket string.

    Kong's pre-function plugin runs under Lua 5.1, whose quoted strings do
    not understand JSON-style ``\\u003c`` escapes. Long-bracket strings keep
    generated HTML literal and avoid per-character escaping drift.
    """
    for delimiter_width in range(1, 8):
        equals = "=" * delimiter_width
        close = f"]{equals}]"
        if close not in value:
            return f"[{equals}[{value}]{equals}]"
    raise ValueError("could not find a safe Lua long-string delimiter")


class KongConfigGenerator:
    """Generates dynamic Kong configuration based on SOURCE values."""
    
    def __init__(self, config_parser):
        """
        Initialize Kong configuration generator.
        
        Args:
            config_parser: ConfigParser instance for accessing environment values
        """
        self.config_parser = config_parser
        self.env_vars = {}
        
    def load_environment_variables(self):
        """Load current environment variables from .env file."""
        self.env_vars = self.config_parser.parse_env_file()
    
    def get_env_value(self, key: str, default: str = "") -> str:
        """
        Get environment variable value.
        
        Args:
            key: Environment variable key
            default: Default value if key not found
            
        Returns:
            str: Environment variable value
        """
        return self.env_vars.get(key, default)

    def _localhost_url(self, port_var: str, default_port) -> str:
        """Build a localhost-source upstream URL from a PORT env var.

        Returns ``http://host.docker.internal:<port>/`` where <port> is
        the value of ``port_var`` in .env if set, else ``default_port``.
        Centralized helper so all the localhost routes share one
        substitution path — drift between them is the bug class memory
        ``feedback_localhost_url_override_symmetry`` warns against.
        """
        port = self.get_env_value(port_var) or str(default_port)
        return f"http://host.docker.internal:{port}/"

    def check_localhost_service(self, host: str, port: int, service_name: str) -> bool:
        """
        Check if a localhost service is available before adding to Kong configuration.
        
        Args:
            host: Host address (e.g., 'localhost', '127.0.0.1')
            port: Port number to check
            service_name: Service name for logging
            
        Returns:
            bool: True if service is reachable, False otherwise
        """
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except (socket.error, socket.timeout):
            print(f"⚠️  {service_name} localhost service not reachable on {host}:{port}")
            print("    Kong route will be created but may fail until service is started")
            return False
    
    def generate_kong_config(self) -> Dict[str, Any]:
        """
        Generate complete Kong configuration based on current SOURCE values.
        
        Returns:
            dict: Complete Kong configuration
        """
        # Load current environment
        self.load_environment_variables()
        
        # Base configuration structure
        config = {
            '_format_version': '2.1',
            '_transform': True,
            'consumers': self.get_consumers(),
            'services': self.get_all_services(),
            # Global Prometheus plugin — exposes /metrics on Kong's Status
            # API (port 8100). Prometheus's observability bundle scrapes it
            # at `kong-api-gateway:8100/metrics`. The plugin is harmless when Prom isn't
            # running; the endpoint just sits unscraped. See the
            # observability bundle spec for the broader scrape topology.
            'plugins': [
                {
                    'name': 'prometheus',
                    'config': {
                        'status_code_metrics': True,
                        'latency_metrics': True,
                        'bandwidth_metrics': True,
                        'upstream_health_metrics': True,
                        'per_consumer': False,
                    },
                },
            ],
        }

        return config
    
    def get_consumers(self) -> List[Dict[str, Any]]:
        """Get Kong consumers configuration.

        Two gotchas worth knowing:

        * **No ``${VAR}`` substitution in Kong DB-less declarative
          config.** The YAML loaded via ``KONG_DECLARATIVE_CONFIG`` is
          taken literally — a value of ``${DASHBOARD_USERNAME}`` becomes
          the literal credential string, not the env value. Resolve the
          credential strings HERE (from .env) before writing the YAML.
        * **ACL plugin checks group membership, not consumer username.**
          A route guarded by ``acl: { allow: [dashboard_user] }`` lets
          through any consumer that belongs to the ``dashboard_user``
          group — NOT the consumer named ``dashboard_user``. The
          consumer must carry an explicit ``acls: [{group: ...}]``
          entry; otherwise basic-auth passes but ACL returns 403.
        """
        # Read from the already-parsed .env snapshot — these values
        # land in the YAML as literal strings; Kong reads them straight
        # into its basic-auth credentials table on startup.
        dashboard_username = self.get_env_value('DASHBOARD_USERNAME', 'kong_admin')
        dashboard_password = self.get_env_value('DASHBOARD_PASSWORD', 'kong_password')
        return [
            {
                'username': 'dashboard_user',
                'basicauth_credentials': [
                    {
                        'username': dashboard_username,
                        'password': dashboard_password,
                    }
                ],
                'acls': [
                    {'group': 'dashboard_user'},
                ],
            }
        ]
    
    def get_all_services(self) -> List[Dict[str, Any]]:
        """
        Get all Kong services based on current SOURCE configurations.

        Returns:
            list: List of Kong service configurations
        """
        services = []

        # Atlas product entrypoint at http://localhost:${KONG_HTTP_PORT}/.
        services.append(self.generate_atlas_root_dashboard_service())

        # Always-containerized Supabase services
        services.extend(self.get_supabase_services())

        # SOURCE-configurable services
        comfyui_service = self.generate_comfyui_service()
        if comfyui_service:
            services.append(comfyui_service)

        n8n_service = self.generate_n8n_service()
        if n8n_service:
            services.append(n8n_service)

        searxng_service = self.generate_searxng_service()
        if searxng_service:
            services.append(searxng_service)

        jupyterhub_service = self.generate_jupyterhub_service()
        if jupyterhub_service:
            services.append(jupyterhub_service)

        openclaw_service = self.generate_openclaw_service()
        if openclaw_service:
            services.append(openclaw_service)

        hermes_service = self.generate_hermes_service()
        if hermes_service:
            services.append(hermes_service)

        tei_reranker_service = self.generate_tei_reranker_service()
        if tei_reranker_service:
            services.append(tei_reranker_service)

        lightrag_service = self.generate_lightrag_service()
        if lightrag_service:
            services.append(lightrag_service)

        minio_service = self.generate_minio_service()
        if minio_service:
            services.append(minio_service)

        minio_s3_service = self.generate_minio_s3_service()
        if minio_s3_service:
            services.append(minio_s3_service)

        ray_service = self.generate_ray_service()
        if ray_service:
            services.append(ray_service)

        prometheus_service = self.generate_prometheus_service()
        if prometheus_service:
            services.append(prometheus_service)

        grafana_service = self.generate_grafana_service()
        if grafana_service:
            services.append(grafana_service)

        spark_master_service = self.generate_spark_master_service()
        if spark_master_service:
            services.append(spark_master_service)

        spark_history_service = self.generate_spark_history_service()
        if spark_history_service:
            services.append(spark_history_service)

        zeppelin_service = self.generate_zeppelin_service()
        if zeppelin_service:
            services.append(zeppelin_service)

        airflow_service = self.generate_airflow_service()
        if airflow_service:
            services.append(airflow_service)

        # Always-containerized adaptive services
        services.extend(self.get_adaptive_services())

        # Alias-only routes for services that didn't previously have a
        # Kong route (Neo4j Browser, Weaviate, Ollama, Doc Processor,
        # LDR, STT, TTS). Without these, the dashboard catch-all used
        # to swallow every unaliased `*.localhost` request.
        services.extend(self.get_alias_only_services())

        return services

    def generate_atlas_root_dashboard_service(self) -> Dict[str, Any]:
        """Serve the generated Atlas service directory at bare Kong root."""
        from utils.atlas_dashboard import build_dashboard_model, render_dashboard_html

        html = render_dashboard_html(build_dashboard_model(
            self.config_parser,
            track_key=getattr(self, "track_key", None),
            overridden_services=getattr(self, "overridden_services", frozenset()),
            hosts_configured=self._hosts_look_configured(),
        ))
        lua_html = _lua_long_string(html)
        return {
            'name': 'atlas-root-dashboard',
            # The route's pre-function exits before proxying. Kong's
            # declarative schema still requires a URL, so point at a
            # loopback discard target that should never be reached.
            'url': 'http://127.0.0.1:1/',
            'routes': [
                {
                    'name': 'atlas-root-dashboard-root',
                    'strip_path': False,
                    'paths': ['/'],
                    'hosts': ['localhost'],
                    'plugins': [
                        {
                            'name': 'pre-function',
                            'config': {
                                'access': [
                                    'return kong.response.exit(200, '
                                    f'{lua_html}, '
                                    '{ ["Content-Type"] = "text/html; charset=utf-8" })'
                                ]
                            },
                        },
                    ],
                },
            ],
            'plugins': [{'name': 'cors'}],
        }

    def _hosts_look_configured(self) -> bool:
        """Best-effort check for wildcard localhost host entries."""
        try:
            hosts_text = Path("/etc/hosts").read_text(encoding="utf-8")
        except OSError:
            return False
        needed = {"supabase-studio.localhost", "chat.localhost", "api.localhost"}
        present = set()
        for line in hosts_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            present.update(parts[1:])
        return bool(needed & present)

    def get_alias_only_services(self) -> List[Dict[str, Any]]:
        """Kong routes for the 7 aliases added in the topology rework
        that lacked dedicated routing logic before.

        Each entry maps an alias hostname to a URL.  When the source is
        a container build, the URL targets the internal docker hostname.
        When the source is ``*-localhost``, we still emit a Kong route —
        the alias remains a useful single-entry-point, and Kong proxies
        through ``host.docker.internal`` to the user's host port.

        ``disabled`` (and unrecognised) sources get no route.

        STT and TTS engines vary (parakeet / speaches / chatterbox /
        whisper-cpp); the container name (and the localhost port) is
        derived from the source-id prefix.
        """
        # Localhost-mode URLs are built via ``_localhost_url`` which reads
        # each service's ``<SVC>_LOCALHOST_PORT`` env var (with the
        # manifest's default-port as fallback). Compose's runtime_sc reads
        # the same PORT var, so Kong and the in-container consumers stay
        # in sync — closing the symmetry gap memory note
        # ``feedback_localhost_url_override_symmetry`` warns against.
        rows: List[tuple] = [
            # (alias, service_name, source_var,
            #  container_url_factory, localhost_url_factory)
            (
                "graph.localhost", "neo4j-browser",
                "NEO4J_GRAPH_DB_SOURCE",
                lambda _src: "http://neo4j-graph-db:7474/",
                lambda _src: self._localhost_url("NEO4J_LOCALHOST_HTTP_PORT", "7474"),
            ),
            (
                "weaviate.localhost", "weaviate-api",
                "WEAVIATE_SOURCE",
                lambda _src: "http://weaviate:8080/",
                lambda _src: self._localhost_url("WEAVIATE_LOCALHOST_PORT", "8080"),
            ),
            (
                "ollama.localhost", "ollama-api",
                "LLM_PROVIDER_SOURCE",
                lambda src: (
                    "http://ollama:11434/"
                    if src and src.startswith("ollama-container")
                    else None
                ),
                # ollama-localhost only.
                lambda src: (
                    self._localhost_url("OLLAMA_LOCALHOST_PORT", "11434")
                    if src == "ollama-localhost" else None
                ),
            ),
            (
                "docling.localhost", "docling-api",
                "DOC_PROCESSOR_SOURCE",
                lambda _src: "http://docling-gpu:8000/",
                lambda _src: self._localhost_url("DOCLING_LOCALHOST_PORT", "63040"),
            ),
            (
                "research.localhost", "research-api",
                "LOCAL_DEEP_RESEARCHER_SOURCE",
                lambda _src: "http://local-deep-researcher:2024/",
                # No localhost source variant defined; the factory is
                # unreachable in practice. Returning None keeps the row
                # skippable if a future manifest adds one without a URL.
                lambda _src: None,
            ),
            (
                "jenkins.localhost", "jenkins",
                "JENKINS_SOURCE",
                lambda _src: "http://jenkins:8080/",
                lambda _src: None,
            ),
            (
                "stt.localhost", "stt-api",
                "STT_PROVIDER_SOURCE",
                self._stt_container_url,
                self._stt_localhost_url,
            ),
            (
                "tts.localhost", "tts-api",
                "TTS_PROVIDER_SOURCE",
                self._tts_container_url,
                self._tts_localhost_url,
            ),
        ]
        services: List[Dict[str, Any]] = []
        for alias, service_name, source_var, container_url_for, localhost_url_for in rows:
            source = (self.get_env_value(source_var) or "").strip()
            if not source or source == "disabled":
                continue
            if "external" in source:
                # external sources point at a remote URL outside our
                # control; no useful Kong route to add here.
                continue
            if "localhost" in source:
                url = localhost_url_for(source)
            else:
                url = container_url_for(source)
            if not url:
                # Source not recognized for this alias — skip silently.
                continue
            services.append({
                "name": service_name,
                "url": url,
                "routes": [{
                    "name": f"{service_name}-all",
                    "strip_path": False,
                    "preserve_host": True,
                    "hosts": [alias],
                }],
                "plugins": [{"name": "cors"}],
            })
        return services

    @staticmethod
    def _stt_container_url(source: str) -> Optional[str]:
        """STT engine container varies by source id prefix."""
        if source == "parakeet-container-gpu":
            return "http://parakeet-gpu:8000/"
        if source.startswith("speaches-container"):
            # speaches-container-cpu and speaches-container-gpu both
            # land on the same `speaches` container; the gpu vs cpu
            # split lives in deploy.resources, not in the hostname.
            return "http://speaches:8000/"
        return None

    def _stt_localhost_url(self, source: str) -> Optional[str]:
        """STT host-install URL — engine-specific PORT env var."""
        if source == "parakeet-localhost":
            return self._localhost_url("PARAKEET_LOCALHOST_PORT", "63042")
        if source == "whisper-cpp-localhost":
            return self._localhost_url("WHISPER_CPP_LOCALHOST_PORT", "63042")
        return None

    @staticmethod
    def _tts_container_url(source: str) -> Optional[str]:
        """TTS engine container varies by source id prefix."""
        if source.startswith("speaches-container"):
            return "http://speaches:8000/"
        if source.startswith("chatterbox-container"):
            # Chatterbox upstream listens on 4123 (compose maps the
            # external CHATTERBOX_PORT to container 4123; tts-provider
            # manifest pins TTS_ENDPOINT=http://chatterbox:4123).
            return "http://chatterbox:4123/"
        return None

    def _tts_localhost_url(self, source: str) -> Optional[str]:
        """TTS host-install URL — engine-specific PORT env var."""
        if source == "chatterbox-localhost":
            return self._localhost_url("CHATTERBOX_LOCALHOST_PORT", "63044")
        return None
    
    def get_supabase_services(self) -> List[Dict[str, Any]]:
        """Get Supabase services (always containerized)."""
        return [
            # Auth services
            {
                'name': 'auth-v1-open',
                'url': 'http://supabase-auth:9999/verify',
                'routes': [
                    {
                        'name': 'auth-v1-open',
                        'strip_path': True,
                        'paths': ['/auth/v1/verify']
                    }
                ],
                'plugins': [{'name': 'cors'}]
            },
            {
                'name': 'auth-v1-open-callback',
                'url': 'http://supabase-auth:9999/callback',
                'routes': [
                    {
                        'name': 'auth-v1-open-callback',
                        'strip_path': True,
                        'paths': ['/auth/v1/callback']
                    }
                ],
                'plugins': [{'name': 'cors'}]
            },
            {
                'name': 'auth-v1-open-authorize',
                'url': 'http://supabase-auth:9999/authorize',
                'routes': [
                    {
                        'name': 'auth-v1-open-authorize',
                        'strip_path': True,
                        'paths': ['/auth/v1/authorize']
                    }
                ],
                'plugins': [{'name': 'cors'}]
            },
            {
                'name': 'auth-v1',
                'url': 'http://supabase-auth:9999/',
                'routes': [
                    {
                        'name': 'auth-v1-all',
                        'strip_path': True,
                        'paths': ['/auth/v1/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'key-auth', 'config': {'key_names': ['apikey']}}
                ]
            },
            # API services
            {
                'name': 'rest-v1',
                'url': 'http://supabase-api:3000/',
                'routes': [
                    {
                        'name': 'rest-v1-all',
                        'strip_path': True,
                        'paths': ['/rest/v1/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'key-auth', 'config': {'key_names': ['apikey']}}
                ]
            },
            {
                'name': 'graphql-v1',
                'url': 'http://supabase-api:3000/rpc/graphql',
                'routes': [
                    {
                        'name': 'graphql-v1-all',
                        'strip_path': True,
                        'paths': ['/graphql/v1/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'key-auth', 'config': {'key_names': ['apikey']}}
                ]
            },
            # Realtime services
            {
                'name': 'realtime-v1-ws',
                'url': 'http://supabase-realtime:4000/socket',
                'protocol': 'ws',
                'routes': [
                    {
                        'name': 'realtime-v1-ws',
                        'strip_path': True,
                        'paths': ['/realtime/v1/']
                    }
                ],
                'plugins': [{'name': 'cors'}]
            },
            {
                'name': 'realtime-v1-rest',
                'url': 'http://supabase-realtime:4000/api',
                'protocol': 'http',
                'routes': [
                    {
                        'name': 'realtime-v1-rest',
                        'strip_path': True,
                        'paths': ['/realtime/v1/api/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'key-auth', 'config': {'key_names': ['apikey']}}
                ]
            },
            # Storage services
            {
                'name': 'storage-v1',
                'url': 'http://supabase-storage:5000/',
                'routes': [
                    {
                        'name': 'storage-v1-all',
                        'strip_path': True,
                        'paths': ['/storage/v1/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'key-auth', 'config': {'key_names': ['apikey']}}
                ]
            },
            # Meta service
            {
                'name': 'meta',
                'url': 'http://supabase-meta:8080/',
                'routes': [
                    {
                        'name': 'meta-all',
                        'strip_path': True,
                        'paths': ['/pg/']
                    }
                ],
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'basic-auth'},
                    {'name': 'acl', 'config': {'allow': ['dashboard_user']}}
                ]
            },
            # Studio dashboard
            {
                'name': 'dashboard',
                'url': 'http://supabase-studio:3000/',
                'routes': [
                    {
                        'name': 'dashboard-all',
                        'strip_path': False,
                        'paths': ['/'],
                        # Restrict the catch-all to the Studio alias.
                        # Previously this
                        # route had NO hosts filter, so every unaliased
                        # request (graph.localhost, weaviate.localhost,
                        # etc.) silently fell through to Studio. The
                        # bare gateway hostname is now owned by the Atlas
                        # root dashboard.
                        'hosts': ['supabase-studio.localhost'],
                    }
                ],
                # basic-auth + ACL, like the /pg/ meta route above and
                # upstream Supabase's own kong template. The dashboard
                # ships no auth of its own (SQL-editor-level DB access),
                # and README/.env document DASHBOARD_USERNAME/PASSWORD as
                # gating Studio — without these plugins that promise was
                # false and Studio sat open on the gateway root.
                'plugins': [
                    {'name': 'cors'},
                    {'name': 'basic-auth'},
                    {'name': 'acl', 'config': {'allow': ['dashboard_user']}}
                ]
            }
        ]
    
    def generate_comfyui_service(self) -> Optional[Dict[str, Any]]:
        """Generate ComfyUI service configuration based on SOURCE."""
        source = self.get_env_value('COMFYUI_SOURCE')
        
        if source == 'disabled':
            return None
            
        service = {
            'name': 'comfyui-api',
            'routes': [
                {
                    'name': 'comfyui-api-all',
                    'strip_path': False,
                    # ComfyUI's web UI emits absolute URLs derived from
                    # the request Host header. Without preserve_host,
                    # Kong forwards Host=comfyui-gpu:18188 and the
                    # browser cannot resolve that internal Docker name.
                    # Same pattern as n8n / LiteLLM / MinIO / Ray.
                    'preserve_host': True,
                    'hosts': ['comfyui.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }
        
        # Dynamic URL based on SOURCE
        if source == 'localhost':
            # Honor COMFYUI_LOCALHOST_PORT so users with a non-default
            # localhost port (.env override) get a Kong route that
            # actually points at their service.
            localhost_url = self._localhost_url('COMFYUI_LOCALHOST_PORT', '8000')
            parsed = urlparse(localhost_url)
            probe_port = parsed.port or 8000
            self.check_localhost_service('localhost', probe_port, 'ComfyUI')
            service['url'] = localhost_url
        elif source in ['container-cpu', 'container-gpu']:
            service['url'] = 'http://comfyui:18188/'
        else:
            # Default to container
            service['url'] = 'http://comfyui:18188/'
        
        return service
    
    def generate_n8n_service(self) -> Optional[Dict[str, Any]]:
        """Generate N8N service configuration based on SOURCE."""
        source = self.get_env_value('N8N_SOURCE')
        
        if source == 'disabled':
            return None
            
        return {
            'name': 'n8n-api',
            'url': 'http://n8n:5678/',
            'connect_timeout': 60000,
            'write_timeout': 60000,
            'read_timeout': 60000,
            'routes': [
                {
                    'name': 'n8n-api-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': ['n8n.localhost']
                }
            ],
            'plugins': [
                {'name': 'cors'},
                {'name': 'request-transformer', 'config': {
                    # Kong DB-less config takes header values literally (no
                    # env interpolation) — resolve the port at generation
                    # time or n8n bakes the unexpanded token into webhook
                    # and editor URLs served via this alias.
                    'add': {'headers': [
                        'X-Forwarded-Host: n8n.localhost:'
                        + (self.get_env_value('KONG_HTTP_PORT')
                           or str(DEFAULT_BASE_PORT))
                    ]}
                }}
            ]
        }
    
    def generate_searxng_service(self) -> Optional[Dict[str, Any]]:
        """Generate SearxNG service configuration based on SOURCE."""
        source = self.get_env_value('SEARXNG_SOURCE')

        if source == 'disabled':
            return None

        return {
            'name': 'searxng-api',
            'url': 'http://searxng:8080/',
            'routes': [
                {
                    'name': 'searxng-api-all',
                    'strip_path': False,
                    # SearXNG's web UI builds redirect / preference URLs
                    # from the Host header. Without preserve_host, Kong
                    # forwards Host=searxng:8080 which the browser then
                    # cannot resolve. Same pattern as n8n / LiteLLM.
                    'preserve_host': True,
                    'hosts': ['search.localhost']
                }
            ],
            'plugins': [
                {'name': 'cors'},
                {
                    'name': 'rate-limiting',
                    'config': {
                        'minute': 60,
                        'hour': 1000,
                        'policy': 'local',
                        # NB: Kong 3.x logs ``config.redis_* is deprecated``
                        # warnings at startup for this plugin even though
                        # ``policy: local`` means none of the redis_* keys
                        # are actually used. Tried providing the new nested
                        # ``redis: {...}`` form — that makes it WORSE
                        # (every flat-key default still gets normalized
                        # and warned, plus the explicit ones). The plugin
                        # schema itself ships these defaults; only Kong 4.0
                        # drops them. Documented in
                        # docs/deployment/expected-startup-warnings.md.
                    }
                }
            ]
        }

    def generate_jupyterhub_service(self) -> Optional[Dict[str, Any]]:
        """Generate JupyterHub service configuration based on SOURCE."""
        source = self.get_env_value('JUPYTERHUB_SOURCE')

        if source == 'disabled':
            return None

        return {
            'name': 'jupyterhub-api',
            'url': 'http://jupyterhub:8888/',
            'routes': [
                {
                    'name': 'jupyterhub-api-all',
                    'strip_path': False,
                    # JupyterHub emits login / spawn redirects derived
                    # from the Host header. Without preserve_host, Kong
                    # forwards Host=jupyterhub:8888 and the browser then
                    # cannot resolve that internal Docker hostname.
                    # Same pattern as n8n / LiteLLM / Ray.
                    'preserve_host': True,
                    'hosts': ['jupyter.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

    def generate_openclaw_service(self) -> Optional[Dict[str, Any]]:
        """Generate OpenClaw service configuration based on SOURCE."""
        source = self.get_env_value('OPENCLAW_SOURCE')

        if source == 'disabled':
            return None

        service = {
            'name': 'openclaw-api',
            'routes': [
                {
                    'name': 'openclaw-api-all',
                    'strip_path': False,
                    'hosts': ['openclaw.localhost'],
                    # OpenClaw ships a browser-facing admin dashboard SPA.
                    # Without preserve_host, Kong forwards the upstream
                    # service's host (openclaw-gateway:18789) and the SPA's
                    # asset URLs / redirects bake that internal hostname,
                    # which is unreachable from the operator's browser.
                    # See reference_kong_preserve_host memory.
                    'preserve_host': True,
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

        # Dynamic URL based on SOURCE
        if source == 'localhost':
            localhost_url = self._localhost_url('OPENCLAW_LOCALHOST_PORT', '63065')
            probe_port = urlparse(localhost_url).port or 63065
            self.check_localhost_service('localhost', probe_port, 'OpenClaw')
            service['url'] = localhost_url
        else:
            service['url'] = 'http://openclaw-gateway:18789/'

        return service

    def generate_hermes_service(self) -> Optional[Dict[str, Any]]:
        """Generate Hermes Agent service configuration based on SOURCE.

        Kong routes traffic to the Hermes dashboard (port 9119), NOT to
        the OpenAI-compatible API (port 8642). Reason: the API is reached
        by other containers via internal DNS (http://hermes:8642) and
        doesn't need a Kong route; the dashboard is a browser UI and
        benefits from the hermes.localhost alias.

        Gated on:
          - HERMES_SOURCE != disabled
          - HERMES_DASHBOARD_ENABLED is truthy (when false, no dashboard
            to route to — drop the route to avoid a 502).
        """
        source = self.get_env_value('HERMES_SOURCE')

        if source == 'disabled':
            return None

        dashboard_enabled = self.get_env_value('HERMES_DASHBOARD_ENABLED', 'true').lower()
        if dashboard_enabled not in ('true', '1', 'yes', 'on'):
            return None

        service = {
            'name': 'hermes-dashboard',
            'routes': [
                {
                    'name': 'hermes-dashboard-all',
                    'strip_path': False,
                    # Hermes dashboard is an SPA that constructs API /
                    # asset URLs from the request Host header. Without
                    # preserve_host, Kong forwards Host=hermes:9119 and
                    # the browser cannot resolve it. The MinIO route's
                    # comment already cites Hermes as an exemplar of
                    # this pattern — restore conformance here.
                    'preserve_host': True,
                    'hosts': ['hermes.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

        if source == 'localhost':
            # The browser-facing dashboard runs on a SEPARATE host port from
            # the API (which goes through HERMES_LOCALHOST_PORT consumed by
            # runtime_sc's HERMES_ENDPOINT). Kong's hermes.localhost alias
            # must target the dashboard; otherwise the user lands on the
            # OpenAI-compatible API JSON instead of the UI. Mirror of the
            # NEO4J_LOCALHOST_HTTP_PORT / NEO4J_LOCALHOST_BOLT_PORT split.
            localhost_url = self._localhost_url('HERMES_LOCALHOST_DASHBOARD_PORT', '63029')
            probe_port = urlparse(localhost_url).port or 63029
            self.check_localhost_service('localhost', probe_port, 'Hermes Dashboard')
            service['url'] = localhost_url
        else:  # container
            service['url'] = 'http://hermes:9119/'

        return service

    def generate_tei_reranker_service(self) -> Optional[Dict[str, Any]]:
        """Kong route for TEI Reranker — pure REST inference, no SPA.

        Routes ``rerank.localhost:${KONG_HTTP_PORT}`` to the TEI
        text-embeddings-inference container at ``http://tei-reranker:80/``.

        Unlike browser-facing SPAs (n8n, LiteLLM, Hermes, etc.), the
        TEI Reranker serves only a JSON REST API. ``preserve_host`` is
        intentionally NOT set (defaults False) — the upstream cares only
        about the request path, not the Host header, and there are no
        redirects to break.

        Gated on ``TEI_RERANKER_SOURCE != disabled``.  When disabled, no
        ``tei-reranker`` container exists and the route would 502 — skip it.
        """
        source = self.get_env_value("TEI_RERANKER_SOURCE", "disabled")
        if not source or source == "disabled":
            return None

        if source == "localhost":
            url = self._localhost_url("TEI_RERANKER_LOCALHOST_PORT", "63031")
        else:  # container-cpu | container-gpu
            url = "http://tei-reranker:80/"

        return {
            "name": "tei-reranker",
            "url": url,
            "routes": [
                {
                    "name": "tei-reranker-all",
                    "strip_path": False,
                    # preserve_host is omitted (defaults False) — REST-only
                    # endpoint; no SPA redirect-URL construction from Host.
                    "hosts": ["rerank.localhost"],
                }
            ],
            "plugins": [{"name": "cors"}],
        }

    def generate_lightrag_service(self) -> Optional[Dict[str, Any]]:
        """Kong route for LightRAG — WebUI SPA at /webui, preserve_host required.

        Routes ``lightrag.localhost:${KONG_HTTP_PORT}`` to the LightRAG
        container at ``http://lightrag:9621/``.

        Why ``preserve_host: True``: LightRAG ships a React-based WebUI at
        ``/webui``. The SPA constructs asset and API URLs from the Host
        header. Without ``preserve_host``, Kong rewrites Host to
        ``lightrag:9621`` and the browser cannot resolve that internal Docker
        hostname. Same pattern as n8n / LiteLLM / Hermes.

        Gated on ``LIGHTRAG_SOURCE != disabled``. When disabled, no
        ``lightrag`` container exists and the route would 502 — skip it.
        """
        source = self.get_env_value("LIGHTRAG_SOURCE", "disabled")
        if not source or source == "disabled":
            return None

        if source == "localhost":
            url = self._localhost_url("LIGHTRAG_LOCALHOST_PORT", "63068")
        else:  # container
            url = "http://lightrag:9621/"

        return {
            "name": "lightrag",
            "url": url,
            "routes": [
                {
                    "name": "lightrag-all",
                    "strip_path": False,
                    # LightRAG WebUI SPA at /webui derives asset and API URLs
                    # from the Host header — preserve_host is mandatory.
                    # See reference_kong_preserve_host memory.
                    "preserve_host": True,
                    "hosts": ["lightrag.localhost"],
                }
            ],
            "plugins": [{"name": "cors"}],
        }

    def generate_minio_service(self) -> Optional[Dict[str, Any]]:
        """Generate MinIO console Kong route.

        Routes ``minio.localhost:${KONG_HTTP_PORT}`` to the MinIO admin
        console on internal port 9001 (host port ``MINIO_CONSOLE_PORT``,
        default 63019; 63018 is the S3 API ``MINIO_PORT``). The S3 API at port 9000 is deliberately NOT
        aliased — S3 clients use full URLs with explicit ports anyway
        and don't benefit from a friendly hostname.

        Why ``preserve_host: True``: MinIO's React console SPA reads
        the Host header to build login redirect / session cookie
        scopes. Without ``preserve_host``, Kong rewrites Host to
        ``minio:9001`` (the upstream) and the browser then can't
        resolve that hostname. Same pattern as n8n / LiteLLM / Hermes.

        Gated on ``MINIO_SOURCE != disabled``. When disabled there's
        no MinIO container to route to and the alias would 502.
        """
        source = self.get_env_value('MINIO_SOURCE')
        if source == 'disabled':
            return None

        return {
            'name': 'minio-console',
            'url': 'http://minio:9001/',
            'routes': [
                {
                    'name': 'minio-console-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': ['minio.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

    def generate_minio_s3_service(self) -> Optional[Dict[str, Any]]:
        """Generate the MinIO S3 API Kong route.

        Routes ``s3.minio.localhost:${KONG_HTTP_PORT}`` to the MinIO S3 API
        on internal port 9000 — a friendly, BASE_PORT-independent host for
        external S3-compatible tools (separate from the console route at
        ``minio.localhost`` -> 9001). Declared as ``extra_kong_aliases`` in
        ``services/minio/service.yml`` so ``--setup-hosts`` wires the host.

        ``preserve_host: True`` keeps the client's signed Host header intact
        so S3 SigV4 validates through the proxy (path-style addressing). The
        host port (``MINIO_PORT``, default 63018) remains the direct,
        proxy-free path and is the recommended one for heavy/upload traffic.

        Gated on ``MINIO_SOURCE != disabled`` (no container to route to
        otherwise — the alias would 502).
        """
        source = self.get_env_value('MINIO_SOURCE')
        if source == 'disabled':
            return None

        return {
            'name': 'minio-s3',
            'url': 'http://minio:9000/',
            'routes': [
                {
                    'name': 'minio-s3-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': ['s3.minio.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

    def generate_ray_service(self) -> Optional[Dict[str, Any]]:
        """Generate Ray dashboard Kong route.

        Routes ``ray.localhost:${KONG_HTTP_PORT}`` to the Ray dashboard
        at ``http://ray-head:8265``.

        Why ``preserve_host: True``: the Ray dashboard SPA constructs
        redirect and asset URLs from the Host header. Without
        ``preserve_host``, Kong rewrites Host to ``ray-head:8265`` and
        the browser cannot resolve that internal Docker hostname. Same
        pattern as n8n / LiteLLM / MinIO.

        Basic-auth is applied using the shared ``DASHBOARD_USERNAME`` /
        ``DASHBOARD_PASSWORD`` credentials (same consumer as the
        Supabase meta route), so the dashboard is not exposed without
        authentication.

        Gated on ``RAY_SOURCE`` ∈ {``ray-container-cpu``,
        ``ray-container-gpu``}. When ``RAY_SOURCE=disabled``, no
        ``ray-head`` container exists and the route would immediately
        502 — so we skip it.
        """
        source = self.get_env_value('RAY_SOURCE')

        if source not in ('ray-container-cpu', 'ray-container-gpu'):
            return None

        return {
            'name': 'ray-dashboard',
            'url': 'http://ray-head:8265/',
            'routes': [
                {
                    'name': 'ray-dashboard-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': ['ray.localhost'],
                }
            ],
            'plugins': [
                {'name': 'cors'},
                {'name': 'basic-auth'},
                {'name': 'acl', 'config': {'allow': ['dashboard_user']}},
            ],
        }

    # ── Uniform SPA-style routes ─────────────────────────────────────
    # Five services share one exact route shape: gate on SOURCE ==
    # 'container', route the whole host with preserve_host (SPAs bake
    # hostnames into redirects/assets — reference_kong_preserve_host)
    # and CORS only. Data-driven so the next observability-style
    # service is one row, not a 26-line method.
    #   (env var, kong service name, upstream url, alias host)
    _SIMPLE_CONTAINER_ROUTES = [
        # Prometheus UI/API: internal-network scrape paths mean the data
        # isn't sensitive behind Kong; pair with Grafana for dashboards.
        ('PROMETHEUS_SOURCE', 'prometheus', 'http://prometheus:9090/',
         'prometheus.localhost'),
        ('SPARK_SOURCE', 'spark-master-ui', 'http://spark-master:8080/',
         'spark.localhost'),
        ('SPARK_SOURCE', 'spark-history-ui', 'http://spark-history:18080/',
         'spark-history.localhost'),
        # Airflow: same alias serves the Web UI and the REST API (/api/v2/).
        ('AIRFLOW_SOURCE', 'airflow', 'http://airflow-webserver:8080/',
         'airflow.localhost'),
        ('ZEPPELIN_SOURCE', 'zeppelin', 'http://zeppelin:8080/',
         'zeppelin.localhost'),
    ]

    def _generate_simple_container_route(
        self, env_var: str, name: str, url: str, host: str,
    ) -> Optional[Dict[str, Any]]:
        """One uniform container-gated SPA route (see table above).

        When the SOURCE isn't `container`, the upstream container doesn't
        exist and the route would 502 — return None to skip it.
        """
        if self.get_env_value(env_var) != 'container':
            return None
        return {
            'name': name,
            'url': url,
            'routes': [
                {
                    'name': f'{name}-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': [host],
                }
            ],
            'plugins': [
                {'name': 'cors'},
            ],
        }

    def generate_prometheus_service(self) -> Optional[Dict[str, Any]]:
        return self._generate_simple_container_route(*self._SIMPLE_CONTAINER_ROUTES[0])

    def generate_spark_master_service(self) -> Optional[Dict[str, Any]]:
        return self._generate_simple_container_route(*self._SIMPLE_CONTAINER_ROUTES[1])

    def generate_spark_history_service(self) -> Optional[Dict[str, Any]]:
        return self._generate_simple_container_route(*self._SIMPLE_CONTAINER_ROUTES[2])

    def generate_airflow_service(self) -> Optional[Dict[str, Any]]:
        return self._generate_simple_container_route(*self._SIMPLE_CONTAINER_ROUTES[3])

    def generate_zeppelin_service(self) -> Optional[Dict[str, Any]]:
        return self._generate_simple_container_route(*self._SIMPLE_CONTAINER_ROUTES[4])

    def generate_grafana_service(self) -> Optional[Dict[str, Any]]:
        """Kong route for Grafana UI.

        `preserve_host: True` is critical — Grafana is an SPA that builds
        redirect URLs from the Host header. Without `preserve_host`, Kong
        rewrites Host to `grafana:3000` and the browser can't resolve it.

        Admin auth is handled by Grafana itself (GF_SECURITY_ADMIN_*), not by
        Kong, so we don't add basic-auth here.

        Gated on `GRAFANA_SOURCE=container`. When disabled, no `grafana`
        container exists and the route would 502 — so we skip it.
        """
        source = self.get_env_value('GRAFANA_SOURCE')
        if source != 'container':
            return None
        return {
            'name': 'grafana',
            'url': 'http://grafana:3000/',
            'routes': [
                {
                    'name': 'grafana-all',
                    'strip_path': False,
                    'preserve_host': True,
                    'hosts': ['grafana.localhost'],
                }
            ],
            'plugins': [
                {'name': 'cors'},
            ],
        }

    def get_adaptive_services(self) -> List[Dict[str, Any]]:
        """Get adaptive services (always containerized when enabled)."""
        services = []

        # Backend API
        backend_service = self.generate_backend_service()
        if backend_service:
            services.append(backend_service)

        # Open WebUI
        openwebui_service = self.generate_openwebui_service()
        if openwebui_service:
            services.append(openwebui_service)

        # LiteLLM gateway — always-on (no SOURCE variation).
        services.append(self.generate_litellm_service())

        return services
    
    def generate_backend_service(self) -> Optional[Dict[str, Any]]:
        """Generate Backend API service configuration based on SOURCE."""
        source = self.get_env_value('BACKEND_SOURCE')
        
        if source == 'disabled':
            return None
            
        return {
            'name': 'backend-api',
            'url': 'http://backend:8000/',
            'routes': [
                {
                    'name': 'backend-api-all',
                    'strip_path': False,
                    'hosts': ['api.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }
    
    def generate_openwebui_service(self) -> Optional[Dict[str, Any]]:
        """Generate Open WebUI service configuration based on SOURCE."""
        source = self.get_env_value('OPEN_WEB_UI_SOURCE')

        if source == 'disabled':
            return None

        return {
            'name': 'openwebui-api',
            'url': 'http://open-web-ui:8080/',
            'routes': [
                {
                    'name': 'openwebui-api-all',
                    'strip_path': False,
                    # Open WebUI is an SPA that derives WebSocket and
                    # asset URLs from the Host header. Without
                    # preserve_host, Kong forwards Host=open-web-ui:8080
                    # which the browser cannot resolve. Same pattern as
                    # n8n / LiteLLM / Ray.
                    'preserve_host': True,
                    'hosts': ['chat.localhost']
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

    def generate_litellm_service(self) -> Dict[str, Any]:
        """Generate LiteLLM gateway Kong route.

        Routes ``litellm.localhost:${KONG_HTTP_PORT}`` to the proxy at
        ``http://litellm:4000/``. The catch-all route covers ``/ui/``
        (admin dashboard), ``/v1/*`` (proxy API), and ``/spend/*``
        (usage telemetry) for callers that prefer a memorable hostname
        over the bare ``localhost:${LITELLM_PORT}``.

        Auto-redirect on ``/``: LiteLLM serves Swagger UI at its root
        and the admin dashboard at ``/ui/``. A bare visit to
        ``http://litellm.localhost:${KONG_HTTP_PORT}/`` would otherwise
        land on Swagger, which is not what operators reaching for the
        alias expect. A ``pre-function`` plugin (allowlisted in
        ``services/kong/compose.yml::KONG_PLUGINS``) short-circuits the
        request with a 302 to ``/ui/`` only when the requested path is
        exactly ``/``. Every other path falls through to the upstream
        unchanged, so ``/v1/*``, ``/spend/*``, and ``/openapi.json``
        still work via the alias. Operators who specifically want
        Swagger UI can still reach it at the direct port
        ``http://localhost:${LITELLM_PORT}/``.

        Always-on — LiteLLM is mandatory in this stack (no SOURCE
        variation, no dashboard-disable toggle). Unlike Hermes, the
        proxy serves both its API and its UI on the same port, so there
        is nothing to gate independently.

        Naming: ``litellm-gateway`` (not ``-api`` like the rest of the
        services and not ``-dashboard`` like Hermes's UI-only route).
        Neither standard label fits — this route exposes BOTH the
        ``/ui/`` admin dashboard AND the ``/v1/*`` proxy API on the
        same upstream. ``-gateway`` captures that dual role.
        """
        return {
            'name': 'litellm-gateway',
            'url': 'http://litellm:4000/',
            'routes': [
                {
                    'name': 'litellm-gateway-all',
                    'strip_path': False,
                    # ``preserve_host: True`` forwards the browser's
                    # original Host header (``litellm.localhost:KONG_HTTP_PORT``)
                    # to LiteLLM. Without this, Kong rewrites Host to
                    # the upstream (``litellm:4000``) and LiteLLM's SPA
                    # uses that internal Docker hostname when
                    # constructing the SSO login redirect, producing
                    # ``http://litellm:4000/ui/login/...`` which the
                    # browser cannot resolve. Same pattern as n8n's
                    # route.
                    'preserve_host': True,
                    'hosts': ['litellm.localhost'],
                    'plugins': [
                        {
                            'name': 'pre-function',
                            'config': {
                                'access': [
                                    'if kong.request.get_path() == "/" then '
                                    'return kong.response.exit(302, "", '
                                    '{ ["Location"] = "/ui/" }) end'
                                ]
                            }
                        }
                    ]
                }
            ],
            'plugins': [{'name': 'cors'}]
        }

    def write_config(self, config: Dict[str, Any], output_path: Path) -> bool:
        """
        Write Kong configuration to YAML file.
        
        Args:
            config: Kong configuration dictionary
            output_path: Path to write configuration file
            
        Returns:
            bool: True if successful
        """
        try:
            # Ensure output directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            
            return True
        except Exception as e:
            print(f"❌ Failed to write Kong configuration: {e}")
            return False
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        Validate Kong configuration.
        
        Args:
            config: Kong configuration to validate
            
        Returns:
            list: List of validation errors (empty if valid)
        """
        errors = []
        
        # Check required top-level fields
        required_fields = ['_format_version', 'services']
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
        
        # Validate services
        services = config.get('services', [])
        for i, service in enumerate(services):
            if 'name' not in service:
                errors.append(f"Service {i} missing name")
            if 'url' not in service:
                errors.append(f"Service {service.get('name', i)} missing URL")
        
        return errors
