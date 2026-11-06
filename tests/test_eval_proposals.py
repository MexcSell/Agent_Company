"""A proposal raised during a benchmark goal is about the platform: its goal runs in the platform project's
repository, not in the eval goal's scratch repository (2026-11-06: eight such goals worked on eval seed repos)."""

from sqlalchemy import select

from agent_company import loop, projects
from agent_company.db import Goal, Project, Proposal, session


def test_a_proposal_from_an_eval_goal_becomes_a_platform_goal(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    projects.seed()
    scratch = tmp_path / "eval-smoke-fix-average"
    scratch.mkdir()
    (scratch / ".git").mkdir()
    eval_goal = loop.create_goal("Fix average", repo_path=str(scratch), origin="eval")
    plain = loop.create_goal("A user's goal", repo_path=str(scratch))
    with session() as s:
        ids = []
        for gid in (eval_goal, plain):
            p = Proposal(goal_id=gid, title=f"Follow-up of {gid}", description="why")
            s.add(p)
            s.flush()
            ids.append(p.id)
        plat = s.scalars(select(Project).where(Project.protected)).first()
        plat_id, plat_path, plat_checks = plat.id, plat.path, list(plat.quality_commands)
    first = projects.decide(ids[0], True, "user:admin")
    second = projects.decide(ids[1], True, "user:admin")
    with session() as s:
        g = s.get(Goal, first["spawned_goal_id"])
        assert g.repo_path == plat_path and g.project_id == plat_id and g.parent_goal_id is None
        assert g.quality_commands == plat_checks and f"#{eval_goal}" in g.description
        other = s.get(Goal, second["spawned_goal_id"])
        assert other.repo_path == str(scratch) and other.parent_goal_id == plain, "a user's goal keeps its repository"
