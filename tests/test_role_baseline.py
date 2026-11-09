"""Every role is covered by every per-role table, its kit names catalog items, and `registry.reestablish` brings
agents back to their role's baseline without taking away what an administrator granted or an agent learned."""

from sqlalchemy import select

from agent_company import blackboard, catalog, org, registry, router, selfevolve
from agent_company.db import Agent, CatalogItem, session


def test_every_per_role_table_covers_every_catalog_role():
    roles = set(org.ROLES)
    for name, table in (("router.DEFAULT_ROUTES", router.DEFAULT_ROUTES), ("router.ROLE_TIERS", router.ROLE_TIERS),
                        ("blackboard.ROLE_SECTIONS", blackboard.ROLE_SECTIONS),
                        ("selfevolve.ROLE_WEIGHTS", selfevolve.ROLE_WEIGHTS), ("org.KITS", org.KITS)):
        assert roles <= set(table), f"{name} lacks {sorted(roles - set(table))}"
    assert set(blackboard.SECTION_OF_ROLE.values()) <= set(blackboard.SECTIONS)
    assert all(set(v) <= set(blackboard.SECTIONS) for v in blackboard.ROLE_SECTIONS.values())
    assert set(router.ROLE_TIERS.values()) <= {"lead", "review", "bulk", "light"}


def test_kits_name_catalog_items_and_every_synonym_names_a_catalog_role():
    builtin = {n for k, n, _, _ in catalog.BUILTIN if k in ("plugin", "skill")}
    for role, kit in org.KITS.items():
        assert set(kit) <= builtin, f"{role}: {sorted(set(kit) - builtin)} not in catalog.BUILTIN"
    for word, role in registry.ROLE_SYNONYMS.items():
        assert role in org.ROLES and word not in org.ROLES, word
    assert all(c.strip() for _, _, _, c in catalog.BUILTIN)


def test_a_synonym_of_a_disabled_role_falls_back_like_the_role():
    role, note = registry.resolve_role("sre")
    assert role == "developer" and "devops" in note and "disabled" in note
    assert registry.resolve_role("security_engineer")[0] == "qa"
    assert registry.resolve_role("researcher")[0] == "research"


def test_a_seeded_agent_has_its_kit_and_the_gm_stays_read_only():
    with session() as s:
        dev = s.scalars(select(Agent).where(Agent.role == "developer")).first()
        gm = s.scalars(select(Agent).where(Agent.role == "general_manager")).first()
        assert list(dev.plugins) == list(org.KITS["developer"])
        assert gm.permissions == ["read"], "the GM is seeded read-only (verification phase 3)"


def test_seed_refreshes_an_untouched_earlier_text_and_keeps_an_edited_one():
    with session() as s:
        rows = {c.name: c for c in s.scalars(select(CatalogItem).where(CatalogItem.kind == "plugin"))}
        rows["context7"].description = "Up-to-date library documentation through the Context7 MCP server."
        rows["context7"].content = "Use the context7 MCP tools to read current docs before using a library API."
        rows["github"].content = "Our own GitHub notes."
    catalog.seed()
    with session() as s:
        rows = {c.name: c for c in s.scalars(select(CatalogItem).where(CatalogItem.kind == "plugin"))}
        assert rows["context7"].content == next(c for k, n, _, c in catalog.BUILTIN if n == "context7")
        assert rows["github"].content == "Our own GitHub notes."


def test_reestablish_restores_the_baseline_and_keeps_grants_and_learning():
    with session() as s:
        by_role = {a.role: a for a in s.scalars(select(Agent))}
        gm, qa, sec, dev = by_role["general_manager"], by_role["qa"], by_role["security"], by_role["developer"]
        gm.permissions, gm.plugins = ["read", "network"], ["pr-review", "pr-review", "own-plugin"]
        qa.skills = {"code-review": 9, "code_review": 6, "testing": 3, "pytest": 8}
        sec.autonomy, dev.permissions = "G3", ["read"]
        ids = gm.id, qa.id, sec.id, dev.id
    preview = registry.reestablish("test", dry_run=True)
    assert {p["id"] for p in preview} >= set(ids)
    with session() as s:
        assert s.get(Agent, ids[0]).permissions == ["read", "network"], "a dry run changes nothing"
    registry.reestablish("test")
    with session() as s:
        gm, qa, sec, dev = (s.get(Agent, i) for i in ids)
        assert gm.permissions == ["read", "network"], "rights an administrator added stay"
        assert dev.permissions == ["read", "write", "shell"], "the template's missing rights return"
        assert gm.plugins[:2] == ["pr-review", "own-plugin"] and set(org.KITS["general_manager"]) <= set(gm.plugins)
        assert gm.plugins.count("pr-review") == 1
        assert qa.skills["code_review"] == 9 and "code-review" not in qa.skills, "one spelling, the higher level"
        assert qa.skills["testing"] == 3 and qa.skills["pytest"] == 8, "levels come from evidence, never raised"
        assert sec.autonomy == "G2", "a disabled gated agent goes back to the gated default"
        assert gm.config_version >= 2
    assert registry.reestablish("test") == [], "idempotent"


def test_a_clone_takes_its_originals_plugins_and_mcp_grants_and_reestablish_fills_missing_ones():
    from agent_company.db import McpGrant, McpServer

    with session() as s:
        srv = McpServer(name="Company bridge", slug="bridge", transport="stdio", command="python", args=["bridge.py"])
        s.add(srv)
        s.flush()
        dev = s.scalars(select(Agent).where(Agent.role == "developer", Agent.parent_id.is_(None))).first()
        s.add(McpGrant(server_id=srv.id, agent_id=dev.id))
        sid, dev_id = srv.id, dev.id
    a = registry.staff("developer", ["python"])
    b = registry.staff("developer", ["python"])
    assert a.id == dev_id and b.parent_id == dev_id
    with session() as s:
        clone = s.get(Agent, b.id)
        assert list(clone.plugins) == list(org.KITS["developer"])
        assert s.scalars(select(McpGrant).where(McpGrant.agent_id == b.id)).one().server_id == sid
        s.query(McpGrant).filter(McpGrant.agent_id == b.id).delete()  # a clone made before clones took grants
    plan = next(p for p in registry.reestablish("test") if p["id"] == b.id)
    assert plan["changes"] == {"mcp_grants": [sid]}
    with session() as s:
        assert s.scalars(select(McpGrant).where(McpGrant.agent_id == b.id)).one().server_id == sid
    assert registry.reestablish("test") == []
