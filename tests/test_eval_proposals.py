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


def _approve_chain(tmp_path, monkeypatch, depth, *, path_only=False, ordinary=False):
    from agent_company.config import settings

    monkeypatch.setattr(loop, "enqueue", lambda gid: None)
    # A depth-2 descendant has depth 3, which the default max_goal_depth=3 blocks.
    monkeypatch.setattr(settings(), "max_goal_depth", 4)
    projects.seed()
    scratch = (settings().workspaces_dir / "eval-smoke-fix-average-1-123"
               if path_only else tmp_path / "repository")
    scratch.mkdir(parents=True)
    (scratch / ".git").mkdir()
    root = loop.create_goal("Original", repo_path=str(scratch),
                            origin="user" if ordinary else "proposal" if path_only else "eval")
    origin = root
    for _ in range(depth):
        origin = loop.create_goal("Follow-up", repo_path=str(scratch), origin="proposal", parent_goal_id=origin)
    with session() as s:
        p = Proposal(goal_id=origin, title="Improve the platform", description="why")
        s.add(p)
        s.flush()
        pid = p.id
        plat = s.scalars(select(Project).where(Project.protected).order_by(Project.id)).first()
        plat_id, plat_path, plat_checks = plat.id, plat.path, list(plat.quality_commands)
    result = projects.decide(pid, True, "user:admin")
    with session() as s:
        goal = s.get(Goal, result["spawned_goal_id"])
        if ordinary:
            assert goal.repo_path == str(scratch)
            assert goal.parent_goal_id == origin
        else:
            assert goal.repo_path == plat_path
            assert goal.project_id == plat_id
            assert goal.parent_goal_id is None
            assert goal.quality_commands == plat_checks
            if path_only:
                assert f"scratch repository {scratch}" in goal.description
            else:
                assert f"descends from benchmark goal #{root}" in goal.description
                assert f"during goal #{origin}" in goal.description


def test_a_proposal_from_a_depth_1_descendant_of_an_eval_goal_becomes_a_platform_goal(tmp_path, monkeypatch):
    _approve_chain(tmp_path, monkeypatch, 1)


def test_a_proposal_from_a_depth_2_descendant_of_an_eval_goal_becomes_a_platform_goal(tmp_path, monkeypatch):
    _approve_chain(tmp_path, monkeypatch, 2)


def test_a_proposal_from_an_eval_workspace_becomes_a_platform_goal(tmp_path, monkeypatch):
    _approve_chain(tmp_path, monkeypatch, 0, path_only=True)


def test_an_ordinary_proposal_chain_keeps_its_repository(tmp_path, monkeypatch):
    _approve_chain(tmp_path, monkeypatch, 1, ordinary=True)
