import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_manager
from services import auth_service


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name
        self.patcher = patch.object(config_manager, "APP_DIR", self.directory)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_default_users_have_secure_password_storage_and_expected_permissions(self):
        users = {item["username"]: item for item in auth_service.list_users()}
        self.assertEqual(set(users), {"admin", "nomina"})
        self.assertTrue(users["admin"]["password_configured"])
        self.assertTrue(users["nomina"]["password_configured"])
        self.assertIn(auth_service.PERM_REPORTS_GENERATE, users["nomina"]["permissions"])
        self.assertNotIn(auth_service.PERM_USERS_MANAGE, users["nomina"]["permissions"])
        with open(auth_service.user_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertNotIn("123456", json.dumps(raw, ensure_ascii=False))

    def test_admin_setup_and_password_authentication(self):
        user = auth_service.authenticate("ADMIN", "admin")
        self.assertEqual(user["username"], "admin")
        self.assertTrue(user["must_change_password"])
        self.assertIn(auth_service.PERM_USERS_MANAGE, user["permissions"])
        auth_service.change_password("admin", "ClaveSegura7")
        changed = auth_service.authenticate("admin", "ClaveSegura7")
        self.assertFalse(changed["must_change_password"])

    def test_legacy_administrator_without_password_is_migrated_to_admin(self):
        os.makedirs(self.directory, exist_ok=True)
        with open(auth_service.user_path(), "w", encoding="utf-8") as handle:
            json.dump({"users": [{"username": "admin", "display_name": "Administrador", "password": None}]}, handle)
        user = auth_service.authenticate("admin", "admin")
        self.assertTrue(user["must_change_password"])

    def test_nomina_initial_password_requires_change_and_can_be_changed(self):
        user = auth_service.authenticate("nomina", "123456")
        self.assertTrue(user["must_change_password"])
        auth_service.change_password("nomina", "NuevaClave9")
        changed = auth_service.authenticate("nomina", "NuevaClave9")
        self.assertFalse(changed["must_change_password"])
        with self.assertRaises(auth_service.AuthError):
            auth_service.authenticate("nomina", "123456")

    def test_user_management_preserves_an_active_admin(self):
        auth_service.change_password("admin", "ClaveSegura7")
        with self.assertRaisesRegex(auth_service.AuthError, "último administrador"):
            auth_service.update_user("admin", active=False)
        created = auth_service.create_user(
            "operador", "Operador", "ClaveSegura7", [auth_service.PERM_USERS_MANAGE], require_password_change=False,
        )
        self.assertEqual(created["username"], "operador")
        disabled = auth_service.update_user("admin", active=False)
        self.assertFalse(disabled["active"])

    def test_malformed_store_is_normalized_with_required_initial_accounts(self):
        os.makedirs(self.directory, exist_ok=True)
        with open(auth_service.user_path(), "w", encoding="utf-8") as handle:
            json.dump({"users": [{"username": "invalid user"}]}, handle)
        users = {item["username"]: item for item in auth_service.list_users()}
        self.assertEqual(set(users), {"admin", "nomina"})


if __name__ == "__main__":
    unittest.main()