"""Learning: agents learn from finished work and share it with related agents.

After every iteration each participating agent gets a lesson, XP and (on success)
skill growth; collaborators are linked; playbooks are curated from lessons. When an
agent starts a task its prompt carries its playbook plus recent lessons of the
agents it is related to, so a newly recruited specialist starts from their experience.
"""

from __future__ import annotations

from sqlalchemy import select

from agent_company import registry
from agent_company.db import Agent, Lesson, session

XP_SUCCESS, XP_FAILURE, XP_PER_LEVEL = 10, 3, 50
PLAYBOOK_LINES = 10


def experience_context(agent_id: int, limit: int = 8) -> str:
    with session() as s:
        agent = s.get(Agent, agent_id)
        if agent is None:
            return ""
        own = s.scalars(select(Lesson).where(Lesson.agent_id == agent_id, Lesson.outcome != "research")
                        .order_by(Lesson.id.desc()).limit(limit // 2)).all()
        lines = [f"- (you) {lesson.text}" for lesson in own]
        for other, weight in registry.related(s, agent_id, 4):
            for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == other.id,
                                                         Lesson.outcome != "research")
                                    .order_by(Lesson.id.desc()).limit(2)):
                lines.append(f"- ({other.name}, similarity {weight:.2f}) {lesson.text}")
        parts = []
        if agent.playbook.strip():
            parts.append("Your playbook:\n" + agent.playbook.strip())
        if lines:
            parts.append("Experience from you and related agents:\n" + "\n".join(lines[:limit]))
        return "\n\n".join(parts)


def learn(agent_id: int, goal_id: int, lesson: str, success: bool, skills: list[str]) -> None:
    with session() as s:
        a = s.get(Agent, agent_id)
        if a is None:
            return
        s.add(Lesson(agent_id=agent_id, goal_id=goal_id, text=lesson.strip()[:1000],
                     outcome="success" if success else "failure"))
        a.xp += XP_SUCCESS if success else XP_FAILURE
        a.level = 1 + a.xp // XP_PER_LEVEL
        if success:
            a.tasks_done += 1
            grown = dict(a.skills or {})
            for sk in skills:
                key = sk.strip().lower().replace(" ", "_")
                if key:
                    grown[key] = min(10, int(grown.get(key, 3)) + 1)
            a.skills = grown
        else:
            a.tasks_failed += 1
        s.flush()
        a.playbook = _curate(s, agent_id)


def _curate(s, agent_id: int) -> str:  # noqa: ANN001
    """Playbook = the most recent distinct lessons, successes as rules, failures as warnings."""
    seen: set[str] = set()
    out: list[str] = []
    for lesson in s.scalars(select(Lesson).where(Lesson.agent_id == agent_id, Lesson.outcome != "research")
                            .order_by(Lesson.id.desc()).limit(40)):
        key = lesson.text.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(("DO: " if lesson.outcome == "success" else "AVOID: ") + lesson.text)
        if len(out) >= PLAYBOOK_LINES:
            break
    return "\n".join(out)


def link_team(agent_ids: list[int]) -> None:
    ids = sorted(set(agent_ids))
    with session() as s:
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                registry.connect(s, a, b, "collaborated", 0.1)
