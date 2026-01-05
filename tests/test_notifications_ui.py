import pathlib
import unittest


class NotificationUiHooksTestCase(unittest.TestCase):
    def test_topbar_contains_notification_hook_elements(self):
        template_path = pathlib.Path("templates/partials/topbar.html")
        content = template_path.read_text(encoding="utf-8")

        self.assertIn("fa-regular fa-bell", content)
        self.assertIn("class=\"counter\"", content)
        self.assertIn("notifications.list_notifications", content)
