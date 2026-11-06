# Images: kept and not kept

## Kept (in this folder)

Diagrams that are part of the specifications themselves (rendered flowcharts and state machines)
and that define behaviour required in `docs/requirements/`. Each is also transcribed as text in
the requirement file that uses it, because agents implementing the requirements cannot view images.

| File | Original | Used in |
| --- | --- | --- |
| `spec01-authorization-flow.png` | ASEAO SPEC01.docx, image4 (§10.1) | SPEC-01 |
| `spec01-spec-dependency-map.png` | ASEAO SPEC01.docx, image8 (§16) | SPEC-01, README |
| `spec02-agent-lifecycle.png` | ASEAO SPEC02.docx, image5 (§6) | SPEC-02 |
| `spec03-recruitment-flow.png` | ASEAO SPEC.03docx.docx, image2 (§2) | SPEC-03 |
| `spec03-candidate-evaluation.png` | ASEAO SPEC.03docx.docx, image3 (§2) | SPEC-03 |
| `spec03-candidate-pipeline.png` | ASEAO SPEC.03docx.docx, image7 (§9.1) | SPEC-03 |
| `spec03-capability-gap-example.png` | ASEAO SPEC.03docx.docx, image4 (§5.1) | SPEC-03 |
| `spec04-workflow-states.png` | ASEAO SPEC04.docx, image14 (§13.1) | SPEC-04 |
| `spec04-agent-execution-loop.png` | ASEAO SPEC04.docx, image13 (§11) | SPEC-04 |
| `spec04-dependency-dag.png` | ASEAO SPEC04.docx, image10 (§7) | SPEC-04 |
| `spec04-budget-allocation.png` | ASEAO SPEC04.docx, image15 (§15.1) | SPEC-04 |
| `spec04-improvement-loop.png` | ASEAO SPEC04.docx, image18 (§20.1) | SPEC-04 |
| `agentforge-architecture.png` | AgentForge-Website-Specification.pdf, page 2 | README (reference architecture) |

## Not kept

Remaining spec diagrams that only restate text (org charts, ER diagram of a PostgreSQL schema not
used here, generic architecture boxes): SPEC01 image1–3, 5, 6; SPEC02 image2, 6–8; SPEC03 image10;
SPEC04 image2, 9, 17. They are still in the original `.docx` files.

Third-party pictures embedded in the specs (blog illustrations, product screenshots, marketing
art). They are illustrative, not requirements, and are copyrighted by their publishers, so they
are not copied into the repository. Sources, as linked next to each picture in the `.docx`:

| Original | Section | Source |
| --- | --- | --- |
| SPEC01 image7 | §12.2 org chart UI | https://ux-design-awards.com/winners/2025-1-an-all-in-one-employee-management-system |
| SPEC02 image1 | §2 architecture | https://medium.com/google-cloud/tutorial-series-gemini-enterprise-agent-platform-part-1-platform-foundations-your-first-4f014ea3517d |
| SPEC02 image4 | §6 lifecycle | https://ejento.ai/blog/managing-ai-workforce-like-human-workforce |
| SPEC02 image9 | §20 advanced direction | https://hundredsolutions.com/blog/ai-orchestration-18/multiagent-ai-orchestration-guide-489 |
| SPEC03 image1 | §2 recruitment | https://www.linkedin.com/pulse/agent-factory-when-ai-agents-recruit-own-colleagues-claude-bjarby-dykee |
| SPEC03 image6, SPEC04 image11 | §8 templates / team formation | https://jpcaparas.medium.com/list/openai-codex-92e77e381b00 |
| SPEC03 image8 | §17 recruitment UI | https://chromosis.com/case-study/custom-web-application/interview-automation |
| SPEC03 image9 | §22 self-expanding workforce | https://medium.com/@fahey_james/the-agentic-mesh-dccc237c7952 |
| SPEC04 image1 | §2 architecture | https://mortalapps.com/blog/best-ai-agent-frameworks/ |
| SPEC04 image3, 5–8 | §4 orchestration models (supervisor-worker, peer-to-peer, hierarchical, blackboard, event-driven) | image-search results, no page URL |
| SPEC04 image12 | §10 blackboard | https://medium.com/@chetankerhalkar/agentic-memory-patterns-context-engineering-the-real-os-of-ai-agents-614cf0cf98b3 |
| SPEC04 image16 | §20 self-evolving orchestration | https://jp.ibtimes.com/fujitsu-develops-self-evolving-multi-agent-ai-enterprise-use-101124 |
| SPEC04 image19 | §22 dashboard | https://dev.to/squarebootstrap/building-a-real-time-ai-agent-dashboard-in-angular-21-how-we-used-signals-onpush-to-ship-a-4hin |
| ASEAO09 image1 | QA dashboard screenshot | image-search result, no page URL |
| `2.pdf` (39 images) | framework logos and README screenshots (Claude Code, AutoGen, CrewAI, LangGraph, Multi-SWE-bench, OpenHands, Open Interpreter, Dify, aider, MetaGPT) | the projects' own sites |
| `Design Autonomous AI Company.pdf` (21 images) | AI-generated illustrations, an A2A explainer, site icons | — |
| `AXoR-Design-System-Requirements.pdf` (1 image) | other product | — |
| `/home/luca/update_2/1/a_1.jpg` … `a_9.jpg` (9 images, not kept) | self-evolving loop, Reflexion, lessons/rules, orchestrator and event bus, memory distillation, MiniMax recursive self-improvement, meta-agent design archive, "loops within loops" delivery system, emergence.ai self-improvement runtime (EVOLUTION-LOOPS.md) | given by the owner without page URLs; a_9 carries emergence.ai's mark |
