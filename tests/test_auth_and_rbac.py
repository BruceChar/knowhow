import unittest
import os
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from src.auth.auth import _session_fingerprint
from src.auth.rbac import User, data_permission_clause, get_current_user


class AuthAndRbacTests(unittest.TestCase):
    def test_session_fingerprint_is_not_reversible_plaintext(self):
        token = "secret-token"
        fingerprint = _session_fingerprint(token)
        self.assertNotEqual(token, fingerprint)
        self.assertEqual(len(fingerprint), 64)

    def test_agent_cannot_see_confidential_content(self):
        clause = data_permission_clause(User("u", "Agent", "agent"), "f")
        self.assertIn("public", clause)
        self.assertIn("internal", clause)
        self.assertNotIn("confidential", clause)

    def test_missing_session_fails_closed_without_dev_auth(self):
        request = Request({
            "type": "http", "method": "GET", "path": "/products",
            "headers": [], "query_string": b"", "scheme": "http",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        })
        with patch.dict(os.environ, {"KNOWHOW_DEV_AUTH": "0"}):
            with self.assertRaises(HTTPException) as ctx:
                get_current_user(request)
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
