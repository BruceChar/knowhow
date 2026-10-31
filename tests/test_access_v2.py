"""Role-code and access-template tests."""
from knowhow.services.team import generate_role_code


def test_role_code_is_generated_without_user_input():
    assert generate_role_code("North America Sales") == "north_america_sales"
    assert generate_role_code("1st Line") == "role_1st_line"


def test_non_ascii_role_name_gets_safe_generated_code():
    code = generate_role_code("北美业务员")
    assert code.startswith("custom_")
    assert len(code) > len("custom_")

