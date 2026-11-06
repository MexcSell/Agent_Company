# Requirements: REQ-009: own domain name for the dashboard

Settings -> Connection accepts domain names (e.g. agent-company.internal). transport.py: include them in the own-CA certificate's subject alternative names (_san ~173, ensure_certs ~220 renews when names change) and in nginx_conf (~290) server_name; IP access keeps working. Validate names. Settings UI in static/admin.js. Document client DNS/hosts-file setup in ReadMe.mdx. Tests for cert names and the nginx config text; do not touch the live nginx in tests.

## Quality commands
- `uv run pytest -q`
