import unittest

from app import create_app
from config import Config
from extensions import db


class LoggingTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class LoggingIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(LoggingTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_request_id_header_and_logging(self):
        with self.assertLogs(self.app.logger.name, level="INFO") as captured:
            response = self.client.get("/auth/login")

        self.assertEqual(response.status_code, 200)
        request_id = response.headers.get("X-Request-ID")
        self.assertTrue(request_id, "Response should include X-Request-ID header")

        logged_request_ids = [
            getattr(record, "request_id", None)
            for record in captured.records
            if record.getMessage() == "request completed"
        ]

        self.assertIn(
            request_id,
            logged_request_ids,
            "Request log entry should include the generated request ID",
        )


if __name__ == "__main__":
    unittest.main()
