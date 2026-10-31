"""Role-code and access-template tests."""
from knowhow.access import can_manage_team
from knowhow.services.team import generate_role_code


def test_role_code_is_generated_without_user_input():
    assert generate_role_code("North America Sales") == "north_america_sales"
    assert generate_role_code("1st Line") == "role_1st_line"


def test_non_ascii_role_name_gets_safe_generated_code():
    code = generate_role_code("北美业务员")
    assert code.startswith("custom_")
    assert len(code) > len("custom_")


def test_team_lead_permission_is_scoped_to_one_team():
    user = {
        "permissions": ["ask.use"],
        "team_roles": [
            {
                "team_id": 10,
                "role_code": "lead",
                "permissions": ["team.lead"],
            },
            {
                "team_id": 20,
                "role_code": "sales",
                "permissions": ["ask.use"],
            },
        ],
    }
    assert can_manage_team(user, 10) is True
    assert can_manage_team(user, 20) is False

