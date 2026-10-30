"""Preview endpoints follow source intent, using real manifest/topology metadata."""

from dataclasses import replace
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from services.topology import get_topology
from start import AtlasStarter
from ui.textual import palette as P
from ui.textual.widgets.info_box import CloudApiSummary, CloudApisRow
from ui.textual.widgets.service_table import ServiceRow, ServiceTable, service_card
from utils.atlas_dashboard import (
    _direct_url, _render_card, build_dashboard_model, render_dashboard_html,
)
from utils.kong_config_generator import KongConfigGenerator
from wizard.model.state_builder import build_app_state, service_extras


class Parser:
    def __init__(self, env):
        self.env = env

    def parse_env_file(self):
        return self.env.copy()

    def parse_service_sources(self):
        return {key: value for key, value in self.env.items() if key.endswith('_SOURCE')}

    def load_consumer_config(self):
        return SimpleNamespace(consumers=[])


CASES = [
    ('none', '0', {}, False),
    ('none', '0', {'CLOUD_OPENAI_SOURCE': 'enabled',
                   'OPENAI_API_KEY': 'synthetic-placeholder'}, False),
    ('none', '0', {'VLLM_METAL_SOURCE': 'managed-localhost'}, False),
    ('none', '1', {}, False),
    ('ollama-container-cpu', '1', {}, True),
    ('ollama-container-gpu', '1', {}, True),
    ('ollama-localhost', '0', {}, True),
    ('disabled', '0', {}, False),
    ('disabled', '1', {}, False),
]


@pytest.fixture(params=CASES, ids=['none-no-upstream', 'none-cloud', 'none-vllm',
                                 'none-stale-scale', 'cpu', 'gpu', 'localhost',
                                 'disabled', 'disabled-stale-scale'])
def preview(request):
    source, scale, extra, available = request.param
    env = {row.source_var: 'disabled' for row in get_topology().rows}
    env.update(BASE_PORT='63000', KONG_HTTP_PORT='63000', OLLAMA_LOCALHOST_PORT='11434',
               VLLM_METAL_LOCALHOST_PORT='8000',
               VLLM_METAL_ENDPOINT='http://host.docker.internal:8000',
               LLM_PROVIDER_SOURCE=source, OLLAMA_SCALE=scale)
    env.update(extra)
    parser = Parser(env)
    state = build_app_state(parser)
    service = next(row for row in state.services if row.name == 'LLM Engine')
    row = ServiceRow(name=service.name, source=service.source, port=service.port or '',
                     alias=service.alias or '', alias_port=state.kong_port,
                     category=service.category, source_options=service_extras(service.name)['options'])
    starter = AtlasStarter.__new__(AtlasStarter)
    starter.config_parser = parser
    starter.hosts_manager = SimpleNamespace(check_missing_hosts=lambda: [],
                                            get_atlas_hosts=lambda: ['ollama.localhost'])
    return SimpleNamespace(row=row, parser=parser, state=state, starter=starter,
                           available=available, source=source, scale=scale)


def _render(renderable):
    stream = StringIO()
    Console(file=stream, width=220, color_system=None).print(renderable)
    return stream.getvalue()


def _table_text(row, monkeypatch):
    table = ServiceTable([row], columns=1)
    monkeypatch.setattr(type(table), 'size', property(lambda self: SimpleNamespace(width=220)))
    return table.render()


def _style_at(text, token):
    return str(text.get_style_at_offset(Console(), text.plain.index(token)))


def test_table_endpoint_and_existing_source_styles(preview, monkeypatch):
    text = _table_text(preview.row, monkeypatch)
    assert ('http://ollama.localhost:63000' in text.plain) is preview.available
    assert 'ollama.localhost:11434' not in text.plain
    assert preview.source in text.plain
    assert _style_at(text, '●') == P.style_for_source_choice(preview.source)
    assert _style_at(text, '▰') == P.style_for_category(preview.row.category)
    expected_name = P.ACCENT if preview.available else P.TEXT_MUTED
    assert _style_at(text, 'LLM Engine') == f'bold {expected_name}'


def test_linear_alias_and_status_follow_source_before_scale(preview):
    summary = preview.starter.build_pre_launch_summary_table()
    table = summary.renderables[0]
    cells = [list(column.cells) for column in table.columns]
    index = cells[1].index('LLM Engine')
    alias = cells[4][index].plain
    status = cells[5][index].plain
    expected = 'ollama.localhost:63000' if preview.available else '-'
    assert alias == expected
    assert cells[3][index].plain == preview.source
    expected_status = {'ollama-container-cpu': '● on', 'ollama-container-gpu': '● GPU',
                       'ollama-localhost': '● local'}.get(preview.source, '● off')
    assert status == expected_status
    line = next(line for line in _render(summary).splitlines() if 'LLM Engine' in line)
    assert ('ollama.localhost:63000' in line) is preview.available


@pytest.mark.parametrize('source', ['none', ' NONE ', 'disabled', ' DISABLED '])
@pytest.mark.parametrize('scale', ['0', '1'])
def test_unavailable_status_overrides_scale(source, scale):
    assert AtlasStarter._get_service_status(source, scale) == ('● off', 'color(245)')


def test_review_and_hover_share_available_endpoint(preview):
    pairs = ServiceTable._tooltip_pairs(preview.row)
    urls = [value for key, value in pairs if key == 'URL']
    assert urls == (['http://ollama.localhost:63000'] if preview.available else [])
    card = service_card(preview.row)
    assert card == ServiceTable._build_tooltip(preview.row)
    assert ('http://ollama.localhost:63000' in card.plain) is preview.available
    assert ('Source', preview.source) in pairs


def test_dashboard_endpoint_and_status_follow_source(preview):
    model = build_dashboard_model(preview.parser)
    row = next(row for row in model.services if row.name == 'LLM Engine')
    assert row.source == preview.source
    assert row.status == ('degraded' if preview.available else 'disabled')
    endpoint = 'http://ollama.localhost:63000' if preview.available else None
    assert row.kong_url == endpoint
    assert row.health_url == endpoint


def test_dashboard_unavailable_card_is_inert_and_not_probed(preview):
    model = build_dashboard_model(preview.parser)
    row = next(row for row in model.services if row.name == 'LLM Engine')
    card = _render_card(row, P.style_for_category(preview.row.category))
    assert ('data-health-url=' in card) is preview.available
    assert ('checking' in card) is preview.available
    assert ('<a class="card"' in card) is preview.available
    assert ('class="card inert"' in card) is (not preview.available)
    assert ('ollama.localhost' in card) is preview.available


def test_dashboard_preserves_unavailable_reason(preview):
    model = build_dashboard_model(preview.parser)
    row = next(row for row in model.services if row.name == 'LLM Engine')
    reasons = {'none': 'No Ollama upstream selected', 'disabled': 'manually-disabled'}
    assert row.disabled_reason == reasons.get(preview.source)


def test_dashboard_active_count_omits_unavailable_engine(preview):
    model = build_dashboard_model(preview.parser)
    active = sum(service.status != 'disabled' for service in model.services)
    assert active == (int(preview.available) + int(preview.parser.env['VLLM_METAL_SOURCE'] != 'disabled'))
    assert f'{active} active</span>' in render_dashboard_html(model)


def test_preview_parity_with_actual_kong_source_selector(preview):
    generator = KongConfigGenerator(preview.parser)
    generator.env_vars = preview.parser.parse_env_file()
    routes = [row for row in generator.get_alias_only_services() if row['name'] == 'ollama-api']
    assert bool(routes) is preview.available
    if preview.available:
        upstream = 'host.docker.internal' if preview.source == 'ollama-localhost' else 'ollama'
        assert routes[0]['url'] == f'http://{upstream}:11434/'
        assert routes[0]['routes'][0]['hosts'] == [preview.row.alias]


def test_cloud_availability_remains_visible_independently(preview):
    cloud = next(row for row in preview.state.cloud_apis if row.name == 'OpenAI')
    enabled = preview.parser.env.get('CLOUD_OPENAI_SOURCE') == 'enabled'
    assert (cloud.enabled, cloud.key_set) == (enabled, enabled)
    cloud_text = CloudApisRow([CloudApiSummary(name=row.name, enabled=row.enabled,
                                             key_set=row.key_set)
                               for row in preview.state.cloud_apis])._build_cloud_apis_text().plain
    linear = _render(preview.starter.build_pre_launch_summary_table())
    if enabled:
        assert 'OpenAI' in cloud_text and 'enabled · key set ✓' in cloud_text
        assert 'enabled · key set ✓' in linear


def test_vllm_source_and_port_remain_independent(preview):
    vllm = next(row for row in preview.state.services if row.name == 'vLLM (Metal)')
    managed = preview.parser.env['VLLM_METAL_SOURCE'] == 'managed-localhost'
    assert vllm.source == ('managed-localhost' if managed else 'disabled')
    assert vllm.port == (':8000' if managed else None)
    dashboard = next(row for row in build_dashboard_model(preview.parser).services
                     if row.name == 'vLLM (Metal)')
    assert dashboard.source == vllm.source
    assert dashboard.status == ('degraded' if managed else 'disabled')
    assert dashboard.direct_url is None  # vLLM has no exposed dashboard port.


@pytest.mark.parametrize('source', ['none', 'disabled', 'ollama-container-cpu'])
def test_pending_precedes_endpoint_rendering(source):
    row = ServiceRow(name='LLM Engine', source=source, port=':11434',
                     alias='ollama.localhost', alias_port='63000', category='llm', pending=True)
    text = ServiceTable([row])._slot_text(row, is_cursor=True, widths=(8, 14, 25, 40))
    assert '◌' in text.plain and 'pending…' in text.plain
    assert '●' not in text.plain and 'ollama.localhost' not in text.plain
    assert '11434' not in text.plain
    assert _style_at(text, '◌') == P.WARN


@pytest.mark.parametrize('source,available', [('none', False), ('disabled', False),
                                             ('ollama-container-cpu', True)])
def test_off_track_keeps_existing_label_and_dimming(source, available):
    row = ServiceRow(name='LLM Engine', source=source, alias='ollama.localhost',
                     alias_port='63000', category='llm', off_track=True)
    text = ServiceTable([row])._slot_text(row, is_cursor=False, widths=(8, 14, 25, 40))
    assert 'disabled (off-track)' in text.plain
    assert ('http://ollama.localhost:63000' in text.plain) is available
    assert _style_at(text, '▰') == f'dim {P.style_for_category("llm")}'
    assert _style_at(text, '●') == f'dim {P.style_for_source_choice(source)}'


@pytest.mark.parametrize('source', ['none', 'disabled'])
def test_unavailable_direct_endpoint_ignores_stale_port(source):
    row = next(row for row in get_topology().rows if row.display_name == 'LLM Engine')
    stale = replace(row, port_var='STALE_PORT')
    assert _direct_url(stale, source, {'STALE_PORT': '11434'}) is None
