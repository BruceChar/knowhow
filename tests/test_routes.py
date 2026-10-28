import unittest

from src.main import app


class RouteTests(unittest.TestCase):
    def test_unified_app_has_authenticated_ask_and_import_routes(self):
        paths = {route.path for route in app.routes}
        for expected in (
            "/v1/ask",
            "/v1/inquiry",
            "/agent",
            "/products",
            "/offers",
            "/api/v1/feedback",
            "/api/v1/feedback/issues",
            "/api/v1/import/templates",
            "/api/v1/import/analyze",
            "/api/v1/import/preview",
            "/api/v1/import/execute",
        ):
            self.assertIn(expected, paths)


if __name__ == "__main__":
    unittest.main()
