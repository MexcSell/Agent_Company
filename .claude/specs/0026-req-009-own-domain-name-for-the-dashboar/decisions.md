# Decisions: REQ-009: own domain name for the dashboard

## Iteration 1: FAIL
- spec (architect): done
- backend (developer): failed
- ui_docs (developer): failed
- qa_review (qa): skipped

Findings:
- task backend: `uv run python -c "from agent_company import transport as t; assert t.parse_domains('Agent-Company.internal, dash.lan')==['agent-company.internal','dash.lan']; [__import__('pytest').raises(ValueError, t.parse_domains, b) for b in ['http://x','*.x','a..b','-a.b','1.2.3.4','x:443','a'*64+'.b','x;y']]"
- task ui_docs: `grep -q 'tls_domains' src/agent_company/static/admin.js && grep -q 'tls-domains' src/agent_company/static/admin.js` exit 1 (L3); `grep -qi 'hosts' ReadMe.mdx && grep -q 'agent-company.internal' ReadMe.mdx` exit 1 (L3)
- task qa_review: skipped: a dependency failed
- command uv run python -c "from agent_company import transport as t; assert t.parse_domains('Agent-Company.internal, dash.lan')==['agent-company.internal','dash.lan']; [__import__('pytest').raises(ValueError, t.parse_domains, b) for b in ['http://x','*.x','a..b','-a.b','1.2.3.4','x:443','a'*64+'.b','x;y']]": `uv run python -c "from agent_company import transport as t; assert t.parse_domains('Agent-Company.internal, dash.lan')==['agent-company.internal','dash.lan']; [__import__('pytest').raises(ValueError, t.parse_domains, b) for b in ['http://x','*.x','a..b','-a.b','1.2.3.4','x:443','a'*64+'.b','x;y']]"
- command grep -q 'tls_domains' src/agent_company/static/admin.js && grep -q 'tls-domains' src/agent_company/static/admin.js: `grep -q 'tls_domains' src/agent_company/static/admin.js && grep -q 'tls-domains' src/agent_company/static/admin.js` exit 1 (L3)
- command grep -qi 'hosts' ReadMe.mdx && grep -q 'agent-company.internal' ReadMe.mdx: `grep -qi 'hosts' ReadMe.mdx && grep -q 'agent-company.internal' ReadMe.mdx` exit 1 (L3)

Next: fix the findings (loop back to Build).
