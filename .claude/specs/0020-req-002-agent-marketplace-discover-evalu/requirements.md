# Requirements: REQ-002: agent marketplace — discover, evaluate, import external agents

Build on recruitment.py (candidates: qualify ~473, approve ~381, reject ~419), agentdefs.py (versioned definitions), catalog.py (source field), evaluation.py and sandbox.py. Import from a repository URL or an uploaded package: fetch as untrusted data into its own empty directory, convert to an agent package manifest (role, instructions, skills, tools, models, dependencies, permissions, version, author, source, licence), create a recruitment candidate that runs its role's eval suite in the L3 sandbox, then administrator approve/reject. Nothing from the package executes on the host; package instructions cannot widen permissions. Store provenance, licence and eval results on the agent definition; rollback via agentdefs versions. Admin API with _audit, CLI, dashboard on the Agents page. New tables/columns in db.py. Update docs/threat-model.md (new integration). Tests offline with a local fixture package.

## Quality commands
- `uv run pytest -q`
