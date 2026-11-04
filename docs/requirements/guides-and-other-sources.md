# Guides, AgentForge website spec, protocols and the original brief

Sources in `/home/luca/update/`:
`1.txt` (original brief and reference URLs);
`Designing_Autonomous_AI_Software_Development_Company.pdf`;
`Design Autonomous AI Company.pdf` (illustrated overview; same organisation as the ASEAO master
spec, no additional requirements);
`AI_Agent_Subscription_and_Free_Specialized_Agents_Guide.pdf`;
`AI_Agent_Company_Download_Guide.pdf`;
`Claude_Code_VSCode_Extensions_CDAF_Guide.pdf`;
`AgentForge-Website-Specification.pdf`;
`agentic-ai-frameworks.pdf` (AWS Prescriptive Guidance on frameworks and protocols);
`2.pdf` (framework logos and README screenshots only, no text).

## The original brief (`1.txt`) — coverage

| Brief item | Where |
| --- | --- |
| Specialised rooms (GM, labs, collection, analysis, introduction, admin, agent management, study, outing, classroom, product, sales, comprehensive analysis, security, network security, guard) | rooms registered in `org.py`; only the core pipeline and research rooms are enabled (CLAUDE.md) |
| A room that retrieves specialised external data in rotation | done (`research.rotation_tick`); shifts and handovers → SPEC-12 S12-R-05, ASEAO06 S06-R-02 |
| Agents cooperate in a circular loop until the goal is met, bringing in external material | done (`loop.py`, research step); durability → SPEC-04 |
| A specialised agent is created per task and linked to similar agents to use their experience | done (`registry._recruit`, `_link_similar`); quality → SPEC-03, SPEC-12 S12-R-02 |
| After completion, agents learn and connect through experience | done (`loop._learn_from`, `link_team`); defects S12-D-01; retrieval → SPEC-11 |
| Come up with new ideas and put them into practice | proposals → goals; documented case → ASEAO10 S10-R-01 |

## Specific requirements

### G-R-01 Task contract minimum fields — Designing guide §3
Add to `contracts.TaskContract` (all optional, defaulted, so existing plans stay valid):
`in_scope`, `out_of_scope` (paths/areas; `out_of_scope` paths become scope violations),
`risk_level` (`low|medium|high`; high adds a security reviewer, ASEAO09 S09-R-02),
`test_plan` (text), `budget` (cost/turns), `deadline`, `requirement_keys` (ASEAO08 S08-R-01).
Task outputs gain `findings` and `next_actions` (parsed like proposals) that feed action items
(ASEAO05 S05-R-04).

### G-R-02 Operational metrics — Designing guide §10, Subscription guide §7
Performance view adds: acceptance-test pass rate, escaped defects (defects reopened after
merge), review rejection rate, median cycle time per goal, blocked time, retry rate, cost per
accepted goal, rollback rate, security findings, human approval latency. Optimise for verified
outcomes, not activity.

### G-R-03 Failure-mode tests — Designing guide §8
`tests/` covers: executor timeout, provider rate limit (429), worker crash mid-goal (restart
resume, SPEC-04 S04-R-05), malformed planner JSON, failing dependency task, merge rollback.

### G-R-04 Measured gate for enabling more roles — Subscription guide §7, `.rtf` §6
"Pipeline reliable" (CLAUDE.md) is defined as: smoke suite pass rate ≥ 80 % over 3 repeats with
the configured models, no open critical defects, retry rate and cost per accepted goal reported.
Only then are gated rooms (analysis, innovation, extra engineering/QA teams) enabled, one at a
time, each with its own eval suite. `eval --compare` before/after must not regress.

### G-R-05 MCP servers for Claude Code agents — CDAF guide §11, §13; `1.txt`; AWS guide
- Administrators register MCP servers (name, command/URL, allowed roles, required env var names —
  never values). For a run, the executor writes a `.mcp.json` into the worktree with only the
  servers allowed for the agent's role and passes `--mcp-config`; tools from MCP servers are
  added to `--allowedTools` explicitly.
- Suggested first servers: Context7 (current docs) for developer/research, Playwright for QA UI
  checks. GitHub MCP is not given to agents (pushing stays on `/sync`, CLAUDE.md).
- Agents cannot add or change MCP servers (SPEC-14 S14-R-02). Changes are audited.

### G-R-06 A2A interoperability — `1.txt`, `.rtf` §15 infrastructure, AWS guide — P2 `open`
- Publish an A2A agent card describing the company's skills and accept A2A tasks as goals for
  authenticated callers only. Not before G-R-04 is met.

### G-R-07 Topics run in order — AgentForge website spec (orchestration)
- Goals in the same project topic (ASEAO05 S05-R-07) run in sequence; different topics run
  concurrently.

### G-R-08 File leases between concurrent goals — AgentForge website spec, step 3
- Goals of the same project run in separate worktrees, so conflicts surface only at merge. Each
  goal declares the paths it expects to change (`allowed_paths` or planner output); a second goal
  declaring overlapping paths waits or is warned. Merge conflicts set the goal to
  `revision_required` instead of failing.

### G-R-09 Mission digest — AgentForge website spec, step 5
- After a goal, the GM posts one digest in the command window: tasks passed/total, duration,
  tokens, cost, merge state. (Verify the existing summary includes tokens; add if missing.)

## Not requirements
- Download guide, CrewAI setup, VS Code extension lists, Hugging Face/Ollama/LM Studio links:
  setup references; local model servers are already supported (`ModelEntry.base_url`).
- The cloned framework repositories next to these documents are code, not specifications; see
  `docs/feature-gap.md` → "Not yet examined".
