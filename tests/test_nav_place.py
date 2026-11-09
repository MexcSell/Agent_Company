"""Run the actual DOM-free navigation placement helper in Node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_company import prefs

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
SOURCE = Path(prefs.__file__).parent / 'static' / 'app.js'


def place(groups, defaults, registered, hidden=None):
    source = SOURCE.read_text()
    match = re.search(r'^function placeNav\(.*?^}', source, re.M | re.S)
    assert match, 'DOM-free placeNav helper missing'
    args = json.dumps([groups, defaults, registered, hidden or []])
    script = match.group() + f'''
const args = {args};
const before = JSON.stringify(args);
const result = placeNav(...args);
if (JSON.stringify(args) !== before) throw new Error("inputs mutated");
console.log(JSON.stringify(result));
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_future_pages_keep_design_positions():
    defaults = prefs.nav_default()['groups']
    future = {'general-manager', 'teams', 'workflows', 'deliverables', 'evaluation', 'organization', 'changes'}
    groups = [{**g, 'items': [i for i in g['items'] if i not in future]} for g in defaults]
    registered = [i for g in groups for i in g['items']]
    result = place(groups, defaults, registered)
    assert result == defaults  # Includes consecutive missing pages in their specified order.
    assert result[0]['items'][:3] == ['dashboard', 'general-manager', 'projects']
    assert result[0]['items'][4:] == ['teams', 'agents', 'companymap', 'workflows', 'deliverables']
    assert result[1]['items'][-2:] == ['evaluation', 'models']
    assert result[2]['items'] == ['evolution', 'organization', 'changes']


def test_missing_predecessors_start_at_zero_and_keep_order():
    defaults = prefs.nav_default()['groups'][2:3]
    groups = [{'key': 'evolution', 'title': 'Evolution', 'items': []}]
    assert place(groups, defaults, ['evolution'])[0]['items'] == ['organization', 'changes']


def test_unknown_page_remains_at_its_groups_end():
    defaults = prefs.nav_default()['groups']
    defaults[0]['items'].append('custom-page')
    groups = [{**g, 'items': [i for i in g['items'] if i != 'custom-page']} for g in defaults]
    registered = [i for g in groups for i in g['items']]
    assert place(groups, defaults, registered) == defaults


def test_hidden_and_registered_pages_are_not_restored():
    defaults = prefs.nav_default()['groups'][2:3]
    groups = [{'key': 'evolution', 'title': 'Custom', 'items': ['changes']}]
    assert place(groups, defaults, ['evolution'], ['organization']) == groups


def sidebar(layout, views):
    """The sidebar's own navGroups (with placeNav) on a saved layout, for an admin; returns ids per group."""
    source = SOURCE.read_text()
    funcs = [re.search(rf'^function {name}\(.*?^}}', source, re.M | re.S) for name in ('placeNav', 'navGroups')]
    assert all(funcs), 'placeNav or navGroups missing'
    defaults = prefs.nav_default()
    script = '\n'.join(m.group() for m in funcs) + f'''
const TOPBAR = ["notifications", "audit", "settings", "users"];
const navDefault = {json.dumps(defaults)};
const VIEW_GROUP = Object.fromEntries(navDefault.groups.flatMap(g => g.items.map(id => [id, g.key])));
const navLayout = {json.dumps(layout)};
const me = {{is_admin: true, ui_mode: "advanced"}};
const VIEWS = {json.dumps([{'id': v, 'title': v} for v in views])};
const g = navGroups();
console.log(JSON.stringify(g.groups.map(x => x.items.map(v => v.id))));
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sidebar_places_new_pages_like_the_editor():
    """navGroups (rendering) and placeNav (saving) agree, also in a group the administrator reordered."""
    layout = prefs.nav_default()
    layout['groups'][0]['items'] = ['projects', 'dashboard', 'goals', 'agents', 'companymap']
    layout['groups'][1]['items'] = ['research', 'knowledge', 'performance', 'models']
    layout['groups'][2]['items'] = ['evolution']
    views = [i for g in prefs.DEFAULT_NAV_GROUPS for i in g['items']] + ['custom-page']
    groups = sidebar(layout, views)
    # general-manager follows dashboard (its predecessor), not "before projects" at the top
    assert groups[0] == ['projects', 'dashboard', 'general-manager', 'goals', 'teams', 'agents', 'companymap',
                         'workflows', 'deliverables']
    assert groups[1] == ['research', 'knowledge', 'performance', 'evaluation', 'models']
    assert groups[2] == ['evolution', 'organization', 'changes']
    assert groups[3][-1] == 'custom-page'  # an unknown id goes to its group's end (system by default)
    editor = place(layout['groups'], prefs.nav_default()['groups'], [])
    assert groups[:3] == [g['items'] for g in editor[:3]]


def test_sidebar_without_any_predecessor_starts_at_zero():
    layout = prefs.nav_default()
    layout['groups'][1]['items'] = ['dashboard']  # every intelligence page moved away, dashboard moved in
    for group in (layout['groups'][0], layout['groups'][3]):
        group['items'] = [i for i in group['items'] if i not in ('dashboard',)]
    layout['groups'][3]['items'] += ['research', 'knowledge', 'performance', 'models']
    groups = sidebar(layout, ['dashboard', 'evaluation', 'research', 'knowledge', 'performance', 'models', 'system'])
    assert groups[1] == ['evaluation', 'dashboard']
    assert 'evaluation' not in groups[3]
