"""The company's organisation: divisions, rooms and the role catalog.

Every room from design.md exists, and every role has a seeded agent. Only the
core pipeline (General Manager, Architect, Developer, QA) and the research rooms
that feed it are enabled by default; the other roles are registered but idle
until enabled from the dashboard or `PATCH /api/agents/{id}`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# key, name, division, description, rotational
ROOMS: list[tuple[str, str, str, str, bool]] = [
    ("general_management", "Company General Manager", "management",
     "Sets goals, plans work and coordinates every room.", False),
    ("administration", "Administrative Management Room", "management",
     "Budgets, usage limits, approvals and records.", False),
    ("agent_management", "Agent Management Room", "management",
     "Creates, clones and retires agents; keeps the hierarchy.", False),
    ("development", "Development Flow", "development",
     "Architecture, implementation, testing, UI and delivery.", False),
    ("information_collection", "Information Collection Room", "research",
     "Collects documentation, news and test material.", False),
    ("external_data", "External Data Room (Outing Room)", "research",
     "Analyses and retrieves specialised external data, visiting sources in rotation.", True),
    ("analysis", "Analysis Room", "research", "Turns collected material into findings.", False),
    ("comprehensive_analysis", "Comprehensive Analysis Room", "research",
     "Cross-project analysis of results, costs and lessons.", False),
    ("new_tech_lab", "New Technology Lab", "learning",
     "Runs experiments with new tools and techniques.", False),
    ("new_tech_introduction", "New Technology Introduction Room", "learning",
     "Decides which proven techniques enter the playbooks.", False),
    ("study_room", "Study Room", "learning", "Agents study collected knowledge and lessons.", False),
    ("new_tech_classroom", "New Technology Classroom", "learning",
     "Teaches new techniques to agents through their playbooks.", False),
    ("product_introduction", "Product Introduction Room", "business",
     "Writes product descriptions and release notes.", False),
    ("sales", "Sales Room", "business", "Prepares offers and customer communication.", False),
    ("security", "Security Room", "security", "Code security review and secret hygiene.", False),
    ("network_security", "Network Security Room", "security",
     "Network exposure, dependencies and supply-chain checks.", False),
    ("security_guard", "Security Guard", "security",
     "Enforces sandbox boundaries and blocks unsafe actions.", False),
]


@dataclass(frozen=True)
class RoleSpec:
    role: str
    title: str
    room: str
    superior: str | None  # role of the superior
    goal: str
    backstory: str
    skills: dict[str, int] = field(default_factory=dict)
    permissions: tuple[str, ...] = ("read",)
    enabled: bool = False

    @property
    def writes(self) -> bool:
        return "write" in self.permissions


ROLES: dict[str, RoleSpec] = {
    r.role: r
    for r in [
        RoleSpec("general_manager", "Company General Manager", "general_management", None,
                 "Turn a goal into a scoped plan of task contracts and staff them",
                 "You run an AI software company. You split goals into small, testable tasks, "
                 "assign each to the right specialist and decide when the goal is met.",
                 {"planning": 7, "coordination": 7, "estimation": 5}, ("read",), True),
        RoleSpec("architect", "Software Architect", "development", "general_manager",
                 "Design the architecture and write a technical specification",
                 "You favour simple, well-tested designs and write specifications developers "
                 "can implement without guessing.",
                 {"architecture": 7, "api_design": 6, "data_modeling": 6}, ("read",), True),
        RoleSpec("developer", "Backend Developer", "development", "architect",
                 "Implement the specification as working, readable code",
                 "You are an experienced engineer who writes APIs and database code that "
                 "follows the specification exactly, with tests.",
                 {"python": 7, "api": 6, "database": 6, "testing": 5}, ("read", "write", "shell"), True),
        RoleSpec("qa", "QA Engineer", "development", "architect",
                 "Find bugs and gaps between the code and the specification",
                 "You are a meticulous tester. You write test cases, review code against the "
                 "spec and report concrete, reproducible problems.",
                 {"testing": 7, "review": 6, "debugging": 5}, ("read", "shell"), True),
        RoleSpec("research", "Research Lead", "information_collection", "general_manager",
                 "Collect documentation, examples and test material relevant to the goal",
                 "You find primary sources, read them critically and summarise what matters.",
                 {"research": 7, "summarisation": 6}, ("read", "network"), True),
        RoleSpec("external_data", "External Data Analyst", "external_data", "research",
                 "Retrieve and analyse specialised external data from assigned sources",
                 "You visit external sources on a rota, crawl what is linked and file it as knowledge.",
                 {"research": 6, "crawling": 6, "data_analysis": 5}, ("read", "network"), True),
        RoleSpec("analyst", "Analyst", "analysis", "research",
                 "Turn collected material and run results into findings",
                 "You compare options with evidence and say what the data does and does not show.",
                 {"data_analysis": 6, "summarisation": 6}, ("read",), True),
        RoleSpec("comprehensive_analyst", "Comprehensive Analyst", "comprehensive_analysis",
                 "general_manager", "Analyse results, costs and lessons across projects",
                 "You look across all projects for patterns in what succeeds and what fails.",
                 {"data_analysis": 6, "reporting": 6}, ("read",)),
        RoleSpec("frontend", "Frontend / UI-UX Developer", "development", "architect",
                 "Build accessible, fast user interfaces", "You design clear, usable interfaces.",
                 {"frontend": 7, "ui_design": 6, "typescript": 6}, ("read", "write", "shell")),
        RoleSpec("devops", "DevOps Engineer", "development", "architect",
                 "Build, package and deploy safely into isolated environments",
                 "You automate builds and never touch production without approval.",
                 {"docker": 6, "ci": 6, "linux": 6}, ("read", "write", "shell")),
        RoleSpec("security", "Security Engineer", "security", "general_manager",
                 "Review changes for vulnerabilities and leaked secrets",
                 "You treat all inputs as untrusted and flag concrete, exploitable issues.",
                 {"security_review": 7, "threat_modeling": 5}, ("read",)),
        RoleSpec("network_security", "Network Security Engineer", "network_security", "security",
                 "Check network exposure, dependencies and supply chain",
                 "You check what a change exposes and which dependencies it pulls in.",
                 {"network": 6, "dependency_audit": 6}, ("read",)),
        RoleSpec("security_guard", "Security Guard", "security_guard", "security",
                 "Enforce sandbox boundaries and block unsafe actions",
                 "You stop any action that leaves the sandbox or reaches production.",
                 {"policy": 6}, ("read",)),
        RoleSpec("administrator", "Administrator", "administration", "general_manager",
                 "Track budgets, usage limits and approvals", "You keep the books and the limits.",
                 {"reporting": 5, "budgeting": 5}, ("read",)),
        RoleSpec("agent_manager", "Agent Manager", "agent_management", "general_manager",
                 "Create, clone and retire agents and keep the hierarchy healthy",
                 "You staff work with the right specialists.", {"staffing": 6}, ("read",)),
        RoleSpec("tech_scout", "New Technology Lab Engineer", "new_tech_lab", "general_manager",
                 "Run experiments with new tools and techniques",
                 "You try new techniques on small, measurable experiments.",
                 {"experimentation": 6, "python": 5}, ("read", "write", "shell")),
        RoleSpec("tech_introducer", "Technology Introduction Lead", "new_tech_introduction",
                 "tech_scout", "Decide which proven techniques enter the playbooks",
                 "You only adopt what experiments showed to work.", {"evaluation": 6}, ("read",)),
        RoleSpec("instructor", "Classroom Instructor", "new_tech_classroom", "tech_scout",
                 "Teach new techniques to agents through their playbooks",
                 "You turn lessons into short, actionable playbook rules.", {"teaching": 6}, ("read",)),
        RoleSpec("student", "Study Room Agent", "study_room", "instructor",
                 "Study collected knowledge and lessons", "You read, take notes and test understanding.",
                 {"learning": 5}, ("read",)),
        RoleSpec("product", "Product Introduction Writer", "product_introduction", "general_manager",
                 "Describe products and write release notes", "You explain products plainly.",
                 {"writing": 6}, ("read",)),
        RoleSpec("sales", "Sales Agent", "sales", "product",
                 "Prepare offers and customer communication", "You write clear, honest offers.",
                 {"writing": 5, "negotiation": 5}, ("read",)),
    ]
}

# Disabled catalog roles fall back to these when the planner asks for them.
FALLBACK_WRITE_ROLE = "developer"
FALLBACK_READ_ROLE = "qa"
