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
