"""Offline navigation migration, validation and administrative audit regressions."""
import re
from copy import deepcopy
from pathlib import Path

import pytest

from agent_company import governance, prefs
from agent_company.db import Agent, Setting, session
from test_simple_mode import app, member  # same offline app and authentication fixture


def test_default_and_legacy_read(app):
    assert prefs.get_ui('nav_layout') == prefs.nav_default()
    old = {'top': ['models'], 'bottom': [], 'hidden': ['knowledge', 'settings']}
    expected = legacy_expected('knowledge')
    assert prefs.set_ui('nav_layout', old) == expected
    assert prefs.get_ui('nav_layout') == expected  # the migrated value reads back unchanged
    with session() as s:
        s.get(Setting, 'ui:nav_layout').value = {'v': {'top': [], 'hidden': ['machines']}}
    assert prefs.get_ui('nav_layout') == legacy_expected('machines')


def legacy_expected(*hidden):
    """The default groups with the hidden ids moved out of them into hidden."""
    expected = prefs.nav_default()
    for group in expected['groups']:
        group['items'] = [i for i in group['items'] if i not in hidden]
    expected['hidden'] = list(hidden)
    return expected


def test_roundtrip_and_audit(app):
    layout = prefs.nav_default()
    layout['groups'][0]['title'] = 'Work'
    layout['groups'][1]['items'].remove('models')
    layout['groups'][1]['items'].remove('knowledge')
    layout['groups'][-1]['items'] += ['models', 'future-page']
    layout['groups'].reverse()
    layout['hidden'] = ['knowledge', 'settings']
    expected = deepcopy(layout)
    expected['hidden'] = ['knowledge']
    response = app.put('/api/ui/nav_layout', json={'value': layout})
    assert response.status_code == 200
    assert response.json()['value'] == expected
    result = app.get('/api/ui/nav_layout').json()
    assert result['value'] == expected
    assert result['default'] == prefs.nav_default()
    assert governance.list_audit(action='ui_layout')[0]['after']['value'] == expected


def test_permissions_and_bad_request(app):
    other, _ = member(app)
    assert other.get('/api/ui/nav_layout').status_code == 200
    assert other.put('/api/ui/nav_layout', json={'value': prefs.nav_default()}).status_code == 403
    assert app.put('/api/ui/nav_layout', json={'value': {'groups': 'bad'}}).status_code == 400


def test_limits():
    value = {'groups': [{'key': 'company', 'title': 'x'*60,
                        'items': ['Bad Id', 'a'*33, 'settings', 'notifications', 'audit', 'users'] +
                                 [f'page-{i}' for i in range(50)]},
                       {'key': 'system', 'title': '', 'items': ['page-0', 'custom']}],
             'hidden': ['settings', 'custom', 'hidden', 'hidden']}
    result = prefs.normalise_nav(value)
    assert len(result['groups'][0]['items']) == 40
    assert result['groups'][0]['title'] == 'x'*40
    assert result['groups'][1] == {'key': 'system', 'title': 'System', 'items': ['custom']}
    assert result['hidden'] == ['hidden']
    assert [g['key'] for g in result['groups']] == ['company', 'system', 'intelligence', 'evolution']
    for bad in (None, [], {}, {'groups': None}, {'groups': [{'key': 'unknown'}]},
                {'groups': [{'key': 'company'}, {'key': 'company'}]}, {'groups': [], 'hidden': None}):
        with pytest.raises(ValueError):
            prefs.normalise_nav(bad)


def test_malformed_values_are_400_and_change_nothing(app):
    saved = prefs.nav_default()
    saved['groups'][0]['title'] = 'Work'
    assert app.put('/api/ui/nav_layout', json={'value': saved}).status_code == 200
    company = {'key': 'company', 'items': []}
    bad = [{'groups': [{'key': key, 'items': []}]} for key in ([], {}, 1, None, ['company'], {'company': 1})]
    bad += [{'groups': [{'items': []}]}, {'groups': ['company']}, {'groups': [['company']]}, {'groups': [None]},
            {'groups': [{**company, 'items': 'dashboard'}]}, {'groups': [{**company, 'items': {'dashboard': 1}}]},
            {'groups': [company], 'hidden': 'models'}, {'groups': [company], 'hidden': {'models': 1}},
            {'groups': 'company'}, {'groups': {'company': []}}, {'groups': None},
            {'top': [], 'hidden': 'models'}, {'hidden': {'models': 1}}]
    for value in bad:
        response = app.put('/api/ui/nav_layout', json={'value': value})
        assert response.status_code == 400, (value, response.status_code)
        assert app.get('/api/ui/nav_layout').json()['value'] == saved, value


def test_non_string_entries_are_skipped(app):
    value = {'groups': [{'key': 'company', 'title': ['x'], 'items': [['dashboard'], {'a': 1}, 1, None, 'projects']}],
             'hidden': [['models'], {'models': 1}, 2, 'models']}
    result = app.put('/api/ui/nav_layout', json={'value': value}).json()['value']
    assert result['groups'][0] == {'key': 'company', 'title': 'Company', 'items': ['projects']}
    assert result['hidden'] == ['models']
    legacy = {'top': [['x']], 'bottom': [{}], 'hidden': [['knowledge'], {'k': 1}, None, 3, 'knowledge', 'audit']}
    expected = legacy_expected('knowledge')
    assert app.put('/api/ui/nav_layout', json={'value': legacy}).json()['value'] == expected
    assert app.get('/api/ui/nav_layout').json()['value'] == expected


def test_default_groups_follow_ui_d_02():
    assert prefs.DEFAULT_NAV_GROUPS == [
        {'key': 'company', 'title': 'Company', 'items': ['dashboard', 'general-manager', 'projects', 'goals', 'teams',
                                                         'agents', 'companymap', 'workflows', 'deliverables']},
        {'key': 'intelligence', 'title': 'Intelligence',
         'items': ['research', 'knowledge', 'performance', 'evaluation', 'models']},
        {'key': 'evolution', 'title': 'Evolution', 'items': ['evolution', 'organization', 'changes']},
        {'key': 'system', 'title': 'System', 'items': ['workspace', 'machines', 'security', 'system']},
    ]
    assert prefs.VIEW_GROUP['simple'] == 'company'
    assert 'simple' not in {i for g in prefs.DEFAULT_NAV_GROUPS for i in g['items']}
    assert prefs.nav_default() == {'groups': prefs.DEFAULT_NAV_GROUPS, 'hidden': []}


def test_app_js_mirror_equals_prefs():
    js = (Path(prefs.__file__).parent / 'static' / 'app.js').read_text()
    block = re.search(r'const NAV_DEFAULT = \{.*?groups:\s*\[(.*?)\],\s*hidden', js, re.S)
    assert block, 'NAV_DEFAULT.groups not found in app.js'
    groups = re.findall(r'\{\s*key:\s*"([^"]+)",\s*title:\s*"([^"]*)",\s*items:\s*\[([^\]]*)\]\s*\}', block.group(1))
    mirror = [{'key': k, 'title': t, 'items': re.findall(r'"([^"]+)"', items)} for k, t, items in groups]
    assert mirror == prefs.DEFAULT_NAV_GROUPS


def test_degraded_agents(app):
    with session() as s:
        s.add(Agent(name='degraded-enabled', role='developer', title='Developer', room_key='engineering', health='degraded', enabled=True))
        s.add(Agent(name='degraded-disabled', role='developer', title='Developer', room_key='engineering', health='degraded', enabled=False))
    assert app.get('/api/overview').json()['degraded_agents'] == 1
