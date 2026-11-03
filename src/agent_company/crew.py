import os

from crewai import LLM, Agent, Crew, Process, Task

from agent_company import router


def llm_for(role: str) -> LLM:
    """The role's model from the Model Router; MODEL from .env when the route is not an LLM."""
    choice = router.resolve(role)
    if choice.kind == "llm":
        return LLM(model=choice.model_id)
    model = os.environ.get("MODEL")
    if not model:
        raise SystemExit(
            f"No API model is available for {role} ({choice.reason}). Set MODEL and its key in .env."
        )
    return LLM(model=model)


def build_crew() -> Crew:
    from agent_company.api import bootstrap

    bootstrap()

    orchestrator = Agent(
        role="CEO / Orchestrator",
        goal="Turn a client request into a clear, scoped project plan with assigned work",
        backstory="You run an AI software company. You plan projects, split them into "
        "tasks and make sure each specialist knows exactly what to deliver.",
        llm=llm_for("general_manager"),
    )
    architect = Agent(
        role="Software Architect",
        goal="Design the architecture and write a technical specification for the plan",
        backstory="You are a senior architect who favours simple, well-tested designs "
        "and writes specifications developers can implement without guessing.",
        llm=llm_for("architect"),
    )
    developer = Agent(
        role="Backend Developer",
        goal="Implement the specification as working, readable code",
        backstory="You are an experienced backend engineer who writes APIs and "
        "database code that follows the specification exactly.",
        llm=llm_for("developer"),
    )
    qa = Agent(
        role="QA Engineer",
        goal="Find bugs and gaps between the code and the specification",
        backstory="You are a meticulous tester. You write test cases, review code "
        "against the spec and report concrete, reproducible problems.",
        llm=llm_for("qa"),
    )

    plan = Task(
        description="Client request: {request}\n\nWrite a project plan: goals, scope, "
        "out-of-scope items, milestones, and which role owns each task.",
        expected_output="A markdown project plan.",
        agent=orchestrator,
        output_file="output/1_plan.md",
    )
    spec = Task(
        description="Using the project plan, write the technical specification: "
        "components, data model, API endpoints and technology choices.",
        expected_output="A markdown technical specification.",
        agent=architect,
        context=[plan],
        output_file="output/2_spec.md",
    )
    code = Task(
        description="Implement the specification. Give each file with its path and "
        "full contents.",
        expected_output="Markdown containing every source file in fenced code blocks.",
        agent=developer,
        context=[spec],
        output_file="output/3_code.md",
    )
    review = Task(
        description="Review the implementation against the specification. Write test "
        "cases and list every bug or missing requirement.",
        expected_output="A markdown QA report with test cases and findings.",
        agent=qa,
        context=[spec, code],
        output_file="output/4_qa.md",
    )

    return Crew(
        agents=[orchestrator, architect, developer, qa],
        tasks=[plan, spec, code, review],
        process=Process.sequential,
        verbose=True,
    )
