# Security and analysis review (iteration 1)

## Security review of 'Carry #337 iteration 2 onto current main (tokens, components, sidebar, nav_layout groups, top bar, hash routes)' (security, done)

## Logic verification

No concrete exploitable vulnerability or leaked secret was found in the reviewed diff. Approval remains blocked by unavailable integration execution.

| Changed functions/data flow | Normal case | Edge/error case | Assessment |
|---|---|---|---|
| `_nav_ids`, `normalise_nav`, `nav_default` | Normalize groups and deduplicate IDs | Invalid group keys raise `ValueError`; current groups validation rejects non-string items; legacy entries are filtered | Fixes the TypeError root cause. Isolated source probes passed |
| `get_ui`, `set_ui`, `ui_get`, `ui_put` | Read normalized preferences; validate before saving; audit successful writes | Invalid saved layout falls back; invalid submission becomes 400; non-admin writes remain 403 | Permission boundary preserved |
| Overview aggregation | Counts enabled degraded agents | Disabled agents excluded; existing authentication retained | Additive response field |
| Theme/accent and layout helpers | Apply predefined browser preferences | Unknown stored values fall back; invalid accents ignored | No arbitrary CSS or script injection |
| Sidebar rendering, grouping, editing, drag/save helpers | Render permitted registered views and persist groups | Hidden/unregistered views preserved; failed saves produce toast | Titles escaped; group keys constrained server-side |
| `show`, hash helpers, `route`, detail renderers | Resolve view/detail routes through existing APIs | Unauthorized views fall back; inaccessible projects fall back; stale detail responses guarded | Hash input does not bypass backend permissions |
| Breadcrumb helpers | Render names as text and internal links | Non-hash breadcrumb URLs are not assigned | Injection boundary preserved |
| Search initialization and handlers | Search permitted sidebar pages and API-returned projects | Fetch failure retains page search; empty results handled | Names use `textContent`; no new external requests |
| Drawer/modal helpers and component preview | Render fixed preview or escaped sidebar markup | Missing drawer and Escape/close paths handled | HTML sink exists, but current callers show no exploitable untrusted flow |
| Appearance/navigation card wiring | Connect controls to existing settings APIs | Save failure handled by shared helper | No new authorization mechanism |

The workspace changed during review: subsequent hardening made groups-form non-string IDs return 400. The final isolated validation probes checked that current behavior.

## Checklist

- **Contract/interfaces:** Groups migration and additive overview/default fields inspected; existing API permission checks retained.
- **Error handling/edge cases:** Malformed keys/items, duplicates, top-bar filtering, roundtrip and legacy migration passed isolated probes.
- **Tests:** Endpoint regressions cover permissions, audit and malformed inputs. Integration execution could not complete here.
- **Dependencies:** No new dependency or build step.
- **Security:** No demonstrated injection, secret exposure, unsafe filesystem/network access or permission regression.
- **Performance:** No evident material regression; project search caches results.
- **Documentation:** WEBSITE.md §3 describes the new shell.

BLOCKER: The required integration command cannot run in this read-only environment: `uv` cannot create its cache lock, and direct pytest execution cannot create temporary files. No integration pass is claimed.

NEXT: Run `uv run pytest -q tests/test_nav_layout.py tests/test_shell_layout.py tests/test_ui_tokens.py tests/test_index_scripts.py tests/test_static_js.py tests/test_company_map.py tests/test_company_config.py` as an explicit integration-stage check in a writable test environment.

MESSAGE fact to qa: Security inspection found no concrete exploit; integration execution remains unverified because this review environment prohibits temporary-file creation.

```json
{"insights":[{"title":"Separate validation evidence from integration evidence","description":"Isolated source probes can verify malformed-input handling in a read-only review, but cannot establish endpoint authorization, persistence or audit behavior.","evidence":["Current normalization probes passed","uv and pytest execution were blocked by filesystem restrictions"],"expected_benefit":"Avoids claiming integration coverage from narrower checks","confidence":0.95}]}
```

OUTCOME: ESCALATED
VERDICT: FAIL
