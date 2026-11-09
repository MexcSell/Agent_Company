"""UI-SET-01: the company's name and timezone are settings every user's browser receives."""

import pytest
from fastapi.testclient import TestClient

from agent_company import api, prefs
from agent_company.auth import DEFAULT_PERMISSIONS

MEMBER = {"id": 2, "name": "m", "is_admin": False, "permissions": dict(DEFAULT_PERMISSIONS)}


def test_defaults_validation_and_overview():
    keys = {d["key"]: d for d in prefs.describe()}
    assert keys["company_name"]["value"] == "Agent Company" and keys["company_timezone"]["value"] == "UTC"
    with pytest.raises(ValueError, match="IANA"):
        prefs.set_value("company_timezone", "Mars/Olympus")
    with pytest.raises(ValueError, match="1-64"):
        prefs.set_value("company_name", "   ")
    prefs.set_value("company_name", "  Acme   Labs ")
    prefs.set_value("company_timezone", "Europe/Berlin")
    app = api.create_app(start_background=False)
    app.dependency_overrides[api.current_user] = lambda: MEMBER
    company = TestClient(app).get("/api/overview").json()["company"]
    assert company == {"name": "Acme Labs", "timezone": "Europe/Berlin"}
