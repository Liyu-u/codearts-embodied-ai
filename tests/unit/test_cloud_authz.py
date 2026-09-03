from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from demo.cloud.auth import (
    Role,
    authorize,
    issue_session,
    revoke_session,
    session_token_hash,
    validate_session,
)
from demo.cloud.credentials import public_credential_configuration
from demo.cloud.service import CloudService


class CloudAuthorizationTests(unittest.TestCase):
    def test_roles_enforce_read_operator_and_admin_boundaries(self) -> None:
        self.assertTrue(authorize(Role.VIEWER, "read"))
        with self.assertRaises(PermissionError):
            authorize(Role.VIEWER, "create_run")
        self.assertTrue(authorize(Role.OPERATOR, "create_run"))
        with self.assertRaises(PermissionError):
            authorize(Role.OPERATOR, "update_configuration")
        self.assertTrue(authorize(Role.ADMIN, "update_configuration"))

    def test_session_store_contains_only_hash_and_relay_token_cannot_authenticate(self) -> None:
        sessions = {}
        issued = issue_session(
            "user-001", Role.OPERATOR, sessions, ttl_ms=5000, now_ms=1000, https=True
        )

        self.assertNotIn(issued.token, sessions)
        self.assertIn(session_token_hash(issued.token), sessions)
        self.assertEqual(validate_session(issued.token, sessions, now_ms=1001).role, Role.OPERATOR)
        with self.assertRaises(PermissionError):
            validate_session("relay-token-is-not-a-browser-session", sessions, now_ms=1001)

    def test_expired_and_revoked_sessions_fail_closed(self) -> None:
        sessions = {}
        expired = issue_session(
            "user-001", Role.VIEWER, sessions, ttl_ms=10, now_ms=100, https=False
        )
        with self.assertRaises(PermissionError):
            validate_session(expired.token, sessions, now_ms=110)

        active = issue_session(
            "user-002", Role.OPERATOR, sessions, ttl_ms=1000, now_ms=200, https=True
        )
        revoke_session(active.token, sessions)
        with self.assertRaises(PermissionError):
            validate_session(active.token, sessions, now_ms=201)

    def test_cookie_policy_is_http_only_strict_and_https_aware(self) -> None:
        secure = issue_session(
            "user-001", Role.ADMIN, {}, ttl_ms=1000, now_ms=100, https=True
        )
        local = issue_session(
            "user-001", Role.ADMIN, {}, ttl_ms=1000, now_ms=100, https=False
        )

        self.assertEqual(secure.cookie["HttpOnly"], True)
        self.assertEqual(secure.cookie["SameSite"], "Strict")
        self.assertEqual(secure.cookie["Secure"], True)
        self.assertEqual(local.cookie["Secure"], False)
        self.assertEqual(secure.cookie["Max-Age"], 1)


class CompetitionAdminSessionTests(unittest.TestCase):
    def build_service(self):
        return CloudService(
            None,
            None,
            relay_token="test-relay-token",
            browser_sessions={},
            now_ms=lambda: 1_000,
        )

    def test_valid_admin_session_overrides_open_access_operator(self) -> None:
        service = self.build_service()

        with patch.dict(
            os.environ,
            {
                "CLOUD_DEMO_OPEN_ACCESS": "1",
                "CLOUD_ADMIN_PASSWORD": "admin-secret-value",
                "CLOUD_OPERATOR_PASSWORD": "operator-secret-value",
            },
            clear=False,
        ):
            public_session = service.current_session(None)
            self.assertEqual(public_session["user"], "competition-demo")
            self.assertEqual(public_session["role"], Role.OPERATOR.value)
            self.assertTrue(public_session["demo_open_access"])

            with self.assertRaises(PermissionError):
                service.authorize_browser(None, "update_configuration")

            issued = service.login("admin", "admin-secret-value")
            self.assertEqual(issued.record.role, Role.ADMIN)

            cookie = f"closed_loop_session={issued.token}"
            admin_session = service.current_session(cookie)

            self.assertEqual(admin_session["user"], "admin")
            self.assertEqual(admin_session["role"], Role.ADMIN.value)
            self.assertTrue(admin_session["demo_open_access"])

            authorized = service.authorize_browser(
                cookie,
                "update_configuration",
            )
            self.assertEqual(authorized.role, Role.ADMIN)

            service.logout(cookie)

            fallback = service.current_session(cookie)
            self.assertEqual(fallback["user"], "competition-demo")
            self.assertEqual(fallback["role"], Role.OPERATOR.value)

    def test_admin_login_rejects_wrong_password(self) -> None:
        service = self.build_service()

        with patch.dict(
            os.environ,
            {
                "CLOUD_ADMIN_PASSWORD": "correct-admin-secret",
            },
            clear=False,
        ):
            with self.assertRaises(PermissionError):
                service.login("admin", "wrong-admin-secret")


class PublicCredentialBoundaryTests(unittest.TestCase):
    def test_public_configuration_reports_presence_without_values(self) -> None:
        public = public_credential_configuration(
            {
                "deepseek_api_key": b"encrypted-deepseek-value",
                "codearts_ak": b"encrypted-ak-value",
                "codearts_sk": None,
            }
        )

        self.assertEqual(
            public,
            {
                "deepseek_api_key": {"configured": True},
                "codearts_ak": {"configured": True},
                "codearts_sk": {"configured": False},
            },
        )
        serialized = repr(public)
        self.assertNotIn("encrypted-deepseek-value", serialized)
        self.assertNotIn("encrypted-ak-value", serialized)


if __name__ == "__main__":
    unittest.main()
