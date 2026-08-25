import tempfile
import unittest
from pathlib import Path

from mercado import create_app
from mercado.db import get_db
from mercado.services.auth import AuthError, create_user, verify_user


class AuthServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.db"),
                "UPLOAD_FOLDER": str(root / "uploads"),
                "NFC_FETCH_ENABLED": False,
                "SKIP_SEED": True,
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_create_user_rejects_short_username(self):
        with self.app.app_context():
            with self.assertRaises(AuthError):
                create_user(get_db(), "ab", "senha1234")

    def test_create_user_rejects_short_password(self):
        with self.app.app_context():
            with self.assertRaises(AuthError):
                create_user(get_db(), "usuario", "curta")

    def test_create_user_rejects_duplicate_username(self):
        with self.app.app_context():
            db = get_db()
            create_user(db, "usuario", "senha1234")
            with self.assertRaises(AuthError):
                create_user(db, "usuario", "outrasenha")

    def test_verify_user_checks_password(self):
        with self.app.app_context():
            db = get_db()
            create_user(db, "usuario", "senha1234")
            self.assertIsNotNone(verify_user(db, "usuario", "senha1234"))
            self.assertIsNone(verify_user(db, "usuario", "senhaerrada"))
            self.assertIsNone(verify_user(db, "inexistente", "senha1234"))

    def test_first_user_inherits_orphaned_receipts_only(self):
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO receipts (source_type, status) VALUES ('manual', 'draft')"
            )
            db.commit()

            first_id = create_user(db, "dono", "senha1234")
            owner = db.execute("SELECT user_id FROM receipts LIMIT 1").fetchone()[
                "user_id"
            ]
            self.assertEqual(owner, first_id)

            create_user(db, "convidado", "senha1234")
            db.execute(
                "INSERT INTO receipts (source_type, status) VALUES ('manual', 'draft')"
            )
            db.commit()
            rows = db.execute("SELECT user_id FROM receipts ORDER BY id").fetchall()
            self.assertEqual(rows[0]["user_id"], first_id)
            self.assertIsNone(rows[1]["user_id"])


if __name__ == "__main__":
    unittest.main()
