"""Admin SPA regression checks for custom-only dialogs."""
from pathlib import Path
import re
import unittest


ADMIN_HTML = Path(__file__).resolve().parent.parent / "src/api/static/admin.html"


class AdminDialogTests(unittest.TestCase):
    def test_no_browser_native_dialogs(self):
        source = ADMIN_HTML.read_text(encoding="utf-8")
        native = re.findall(
            r"(?<![\w.])(?:window\.)?(alert|confirm|prompt)\s*\(",
            source,
        )
        self.assertEqual(native, [])

    def test_feedback_uses_custom_form_card(self):
        source = ADMIN_HTML.read_text(encoding="utf-8")
        self.assertIn("async function sendAgentFeedback", source)
        self.assertIn("title:'提交反馈 · 需要修正'", source)
        self.assertIn("function customForm", source)
        self.assertIn("function customConfirm", source)


if __name__ == "__main__":
    unittest.main()
