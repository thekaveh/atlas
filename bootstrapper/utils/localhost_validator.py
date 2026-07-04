"""
Localhost service validation utilities.

Validates that localhost services are accessible when configured as SOURCE=localhost.
"""

import socket
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from core.config_parser import ConfigParser


class LocalhostValidator:
    """Validates localhost services accessibility."""
    
    # Service validation configurations
    SERVICE_CHECKS = {
        # All single-engine services read their *_LOCALHOST_PORT override
        # (the same var runtime_sc, Kong's _localhost_url, and
        # service_config read) so a user-set port doesn't produce a false
        # "not detected" warning — the asymmetric-override class tracked
        # in feedback_localhost_url_override_symmetry.md.
        'LLM_PROVIDER_SOURCE': {
            'source_values': ['ollama-localhost'],
            'check_type': 'http',
            'port_env_var': 'OLLAMA_LOCALHOST_PORT',
            'health_path': '/api/tags',
            'service_name': 'Ollama',
            'default_port': 11434
        },
        'COMFYUI_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'COMFYUI_LOCALHOST_PORT',
            'health_path': '/system_stats',
            'service_name': 'ComfyUI',
            'default_port': 8000
        },
        'WEAVIATE_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'WEAVIATE_LOCALHOST_PORT',
            'health_path': '/v1/schema',
            'service_name': 'Weaviate',
            'default_port': 8080
        },
        'NEO4J_GRAPH_DB_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'tcp',
            'port_env_var': 'NEO4J_LOCALHOST_BOLT_PORT',
            'service_name': 'Neo4j',
            'default_port': 7687
        },
        # STT and TTS providers use per-source configs because each
        # localhost variant runs a *different* binary with a different port
        # (read from its own PORT env var) and a different health-probe path
        # (Parakeet MLX has /health, whisper.cpp-server only has /inference
        # and /load — no health endpoint — so we TCP-probe it instead).
        # Symmetric story on TTS: Chatterbox exposes /health, so HTTP-probe.
        'STT_PROVIDER_SOURCE': {
            'per_source': {
                'parakeet-localhost': {
                    'check_type': 'http',
                    'port_env_var': 'PARAKEET_LOCALHOST_PORT',
                    'health_path': '/health',
                    'default_port': 63042,
                    'service_name': 'Parakeet STT (host-side)',
                    'hint': 'Start the Parakeet MLX/native server — see services/parakeet/provider/mlx/README.md.',
                },
                'whisper-cpp-localhost': {
                    # whisper.cpp's whisper-server has no /health endpoint
                    # (only /inference and /load). Fall back to a TCP probe
                    # against the configured port so we at least catch
                    # "server not running" without false-negatives on the
                    # unsupported health URL.
                    'check_type': 'tcp',
                    'port_env_var': 'WHISPER_CPP_LOCALHOST_PORT',
                    'default_port': 63042,
                    'service_name': 'whisper.cpp STT (host-side)',
                    'hint': 'Start whisper-server — see services/parakeet/provider/whisper-cpp/README.md.',
                },
            },
        },
        'TTS_PROVIDER_SOURCE': {
            'per_source': {
                'chatterbox-localhost': {
                    'check_type': 'http',
                    'port_env_var': 'CHATTERBOX_LOCALHOST_PORT',
                    'health_path': '/health',
                    'default_port': 63044,
                    'service_name': 'Chatterbox TTS (host-side)',
                    'hint': 'Start the Chatterbox server — see services/tts-provider/provider/localhost/README.md.',
                },
            },
        },
        # NOTE: these three localhost-source services follow the same
        # asymmetric-override pattern documented in
        # feedback_localhost_url_override_symmetry.md. The probe MUST use
        # the user-overridable `<X>_LOCALHOST_PORT` (which the wizard
        # writes), NOT the container's host-bound port var
        # (DOC_PROCESSOR_PORT / OPENCLAW_GATEWAY_PORT / HERMES_API_PORT).
        # Reading the wrong var here makes the validator probe the
        # wrong port -- either false "not running" warnings or false
        # positives against an unrelated process. service_config.py was
        # fixed for the same class in commit 1682801; this completes the
        # symmetry on the validator side.
        'DOC_PROCESSOR_SOURCE': {
            'source_values': ['docling-localhost'],
            'check_type': 'http',
            'port_env_var': 'DOCLING_LOCALHOST_PORT',
            'service_name': 'Docling Document Processor',
            'default_port': 63059
        },
        'TIKA_SOURCE': {
            'source_values': ['tika-localhost'],
            'check_type': 'http',
            'port_env_var': 'TIKA_LOCALHOST_PORT',
            'health_path': '/tika',
            'service_name': 'Apache Tika',
            'default_port': 9998,
            'hint': 'Start the Apache Tika server and ensure it listens on TIKA_LOCALHOST_PORT.',
        },
        'OPENCLAW_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'OPENCLAW_LOCALHOST_PORT',
            'service_name': 'OpenClaw Gateway',
            'default_port': 63065
        },
        'HERMES_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'HERMES_LOCALHOST_PORT',
            'service_name': 'Hermes Agent',
            'default_port': 8642
        },
        'TEI_RERANKER_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'TEI_RERANKER_LOCALHOST_PORT',
            'service_name': 'TEI Reranker',
            'default_port': 63049
        },
        'LIGHTRAG_SOURCE': {
            'source_values': ['localhost'],
            'check_type': 'http',
            'port_env_var': 'LIGHTRAG_LOCALHOST_PORT',
            'service_name': 'LightRAG',
            'default_port': 63068
        }
    }
    
    def __init__(self, config_parser: Optional[ConfigParser] = None):
        """
        Initialize localhost validator.
        
        Args:
            config_parser: ConfigParser instance for reading service sources
        """
        self.config_parser = config_parser or ConfigParser()
        
    def check_http_endpoint(self, url: str, timeout: int = 5) -> bool:
        """
        Check if an HTTP endpoint is accessible.
        
        Args:
            url: URL to check
            timeout: Connection timeout in seconds
            
        Returns:
            bool: True if accessible
        """
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status < 400
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout):
            return False
            
    def check_tcp_port(self, host: str, port: int, timeout: int = 5) -> bool:
        """
        Check if a TCP port is accessible.
        
        Args:
            host: Host to check
            port: Port to check
            timeout: Connection timeout in seconds
            
        Returns:
            bool: True if accessible
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                result = sock.connect_ex((host, port))
                return result == 0
        except Exception:
            return False
            
    def _resolve_source_config(self, source_var: str, source_value: str) -> Optional[Dict]:
        """Pick the right SERVICE_CHECKS entry for this source.

        Returns ``None`` if the source isn't one we validate. Handles two
        shapes:

        * Legacy flat shape — top-level keys (``check_type``, ``service_name``,
          etc.) shared by every source listed in ``source_values``. Used for
          all the single-engine services (ComfyUI, Weaviate, Neo4j, …).
        * Per-source shape — ``per_source: {source_value: {...}}``. Used for
          STT/TTS providers where each localhost variant runs a different
          server with its own URL env var, probe protocol, and default port.
        """
        if source_var not in self.SERVICE_CHECKS:
            return None
        top = self.SERVICE_CHECKS[source_var]
        if 'per_source' in top:
            return top['per_source'].get(source_value)
        if source_value in top.get('source_values', []):
            return top
        return None

    def validate_service(self, source_var: str, source_value: str) -> Tuple[bool, List[str]]:
        """
        Validate a specific service configuration.

        Args:
            source_var: SOURCE variable name (e.g., 'COMFYUI_SOURCE')
            source_value: SOURCE value (e.g., 'localhost')

        Returns:
            tuple: (is_valid, messages)
        """
        config = self._resolve_source_config(source_var, source_value)
        if config is None:
            return True, []  # Not a localhost source we validate

        service_name = config['service_name']
        messages: List[str] = []

        if config['check_type'] == 'http':
            # HTTP endpoint validation. Two ways the endpoint URL is built:
            #
            # 1. ``port_env_var`` names a PORT env var (e.g.
            #    ``WHISPER_CPP_LOCALHOST_PORT=63042`` or
            #    ``DOCLING_LOCALHOST_PORT=63059``). The probe URL is
            #    ``http://localhost:<port><health_path|/health>``. STT/TTS
            #    use per-source ``LOCALHOST_PORT`` vars because each
            #    localhost variant has its own port; the same var that
            #    compose's runtime_sc and Kong's _localhost_url helper read.
            #
            # 2. ``endpoints`` — a literal URL list. No SERVICE_CHECKS
            #    entry uses it anymore (every service reads its
            #    *_LOCALHOST_PORT var); kept as the escape hatch for
            #    ad-hoc checks.
            if 'port_env_var' in config:
                env_vars = self.config_parser.parse_env_file()
                # `or default`: dict.get's default only applies when the
                # key is ABSENT — a present-but-blank var would otherwise
                # produce http://localhost:/path and a false warning.
                port = (env_vars.get(config['port_env_var']) or "").strip() or config['default_port']
                endpoints = [f"http://localhost:{port}{config.get('health_path', '/health')}"]
            else:
                endpoints = config['endpoints']

            accessible = False

            for endpoint in endpoints:
                if self.check_http_endpoint(endpoint):
                    accessible = True
                    messages.append(f"✅ Localhost {service_name} service is accessible at {endpoint}")
                    break

            if not accessible:
                endpoint_list = ', '.join(endpoints)
                messages.append(f"⚠️  Warning: {service_name} not detected at {endpoint_list}")
                messages.append(f"   Make sure {service_name} is running locally before starting the stack")

                # Per-source hint (set on STT/TTS entries pointing at their localhost docs)
                hint = config.get('hint')
                if hint:
                    messages.append(f"   {hint}")
                elif source_var == 'COMFYUI_SOURCE':
                    messages.append(f"   Please start ComfyUI locally with: python main.py --listen --port {port}")
                    messages.append("   Or refer to the documentation for installation instructions.")

            return accessible, messages

        elif config['check_type'] == 'tcp':
            # TCP port validation. Used when the server has no health-style
            # GET endpoint (whisper-server only has /inference and /load,
            # both POST-only; a GET probe would return 405, which the HTTP
            # check treats as "down").
            if 'port_env_var' in config:
                env_vars = self.config_parser.parse_env_file()
                port = (env_vars.get(config['port_env_var']) or "").strip() or config['default_port']
                host = 'localhost'
            else:
                host = config['host']
                port = config['port']

            # socket.connect_ex needs an int; env reads yield strings and
            # a str port made the probe raise (swallowed) → always-fail.
            try:
                port = int(str(port).strip())
            except ValueError:
                port = int(config['default_port'])

            accessible = self.check_tcp_port(host, port)

            if accessible:
                messages.append(f"✅ Localhost {service_name} service is reachable at {host}:{port}")
            else:
                messages.append(f"⚠️  Warning: {service_name} not detected at {host}:{port}")
                messages.append(f"   Make sure {service_name} is running locally before starting the stack")
                hint = config.get('hint')
                if hint:
                    messages.append(f"   {hint}")

            return accessible, messages

        return True, []
        
    def validate_all_localhost_services(self) -> Dict[str, Tuple[bool, List[str]]]:
        """
        Validate all localhost services based on current configuration.
        
        Returns:
            dict: Dictionary mapping service names to (is_valid, messages) tuples
        """
        service_sources = self.config_parser.parse_service_sources()
        results = {}
        
        for source_var, source_value in service_sources.items():
            is_valid, messages = self.validate_service(source_var, source_value)
            if messages:  # Only include services that have validation messages
                results[source_var] = (is_valid, messages)
                
        return results
        
    def get_localhost_services(self) -> List[Tuple[str, str]]:
        """
        Get list of services configured to use localhost.

        Returns:
            list: List of (source_var, source_value) tuples for localhost services
        """
        service_sources = self.config_parser.parse_service_sources()
        localhost_services = []

        for source_var, source_value in service_sources.items():
            # Handles both legacy flat (``source_values``) and per-source shapes.
            if self._resolve_source_config(source_var, source_value) is not None:
                localhost_services.append((source_var, source_value))

        return localhost_services
        
    def has_localhost_services(self) -> bool:
        """
        Check if any services are configured to use localhost.
        
        Returns:
            bool: True if any localhost services are configured
        """
        return len(self.get_localhost_services()) > 0
        
