import datetime as dt
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest

import pandas as pd

from models.database_manager import (
    add_new_inventory_item,
    add_sales_record,
    bulk_import_sales,
    change_user_password,
    connect_db,
    create_user_session,
    delete_transaction,
    get_db_path,
    get_user_profile,
    get_user_by_session,
    init_db,
    list_users,
    register_user,
    reset_user_password,
    set_user_admin,
    update_reorder_point,
    verify_user,
)


class DatabaseManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        self.old_db_path = os.environ.get("INVENTORY_DB_PATH")
        os.chdir(self.temp_dir.name)
        os.environ["INVENTORY_DB_PATH"] = os.path.join(self.temp_dir.name, "inventory_system.db")
        init_db()
        self.user_id = 1

    def tearDown(self):
        os.chdir(self.old_cwd)
        if self.old_db_path is None:
            os.environ.pop("INVENTORY_DB_PATH", None)
        else:
            os.environ["INVENTORY_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    def read_stock(self, product):
        conn = sqlite3.connect(get_db_path())
        row = conn.execute(
            "SELECT current_stock FROM inventory WHERE user_id = ? AND product = ?",
            (self.user_id, product),
        ).fetchone()
        conn.close()
        return row[0]

    def test_duplicate_product_is_rejected(self):
        self.assertTrue(add_new_inventory_item(self.user_id, "Widget", 10, 5))
        self.assertFalse(add_new_inventory_item(self.user_id, "Widget", 20, 5))
        self.assertEqual(self.read_stock("Widget"), 10)

    def test_sale_deducts_stock_and_rejects_oversell(self):
        add_new_inventory_item(self.user_id, "Widget", 10, 5)

        self.assertTrue(add_sales_record(self.user_id, "Widget", dt.date.today(), 4))
        self.assertEqual(self.read_stock("Widget"), 6)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(add_sales_record(self.user_id, "Widget", dt.date.today(), 7))
        self.assertEqual(self.read_stock("Widget"), 6)

    def test_delete_sale_restores_stock(self):
        add_new_inventory_item(self.user_id, "Widget", 10, 5)
        add_sales_record(self.user_id, "Widget", dt.date.today(), 4)

        conn = sqlite3.connect(get_db_path())
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]
        conn.close()

        self.assertTrue(delete_transaction("sales", sale_id, self.user_id))
        self.assertEqual(self.read_stock("Widget"), 10)

    def test_bulk_import_sales_creates_inventory_and_skips_bad_rows(self):
        import_df = pd.DataFrame([
            {
                "date": dt.date.today().isoformat(),
                "product": "Widget",
                "quantity": 4,
                "current_stock": 20,
                "reorder_point": 5,
            },
            {
                "date": dt.date.today().isoformat(),
                "product": "Broken",
                "quantity": 0,
                "current_stock": 5,
                "reorder_point": 2,
            },
        ])

        result = bulk_import_sales(self.user_id, import_df)

        self.assertTrue(result["success"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.read_stock("Widget"), 20)

    def test_bulk_import_skips_duplicate_sales(self):
        import_df = pd.DataFrame([
            {
                "date": dt.date.today().isoformat(),
                "product": "Widget",
                "quantity": 4,
                "current_stock": 20,
                "reorder_point": 5,
            }
        ])

        first_result = bulk_import_sales(self.user_id, import_df)
        second_result = bulk_import_sales(self.user_id, import_df)

        self.assertTrue(first_result["success"])
        self.assertEqual(first_result["imported"], 1)
        self.assertFalse(second_result["success"])
        self.assertEqual(second_result["imported"], 0)
        self.assertEqual(second_result["skipped"], 1)

    def test_update_reorder_point(self):
        add_new_inventory_item(self.user_id, "Widget", 10, 5)

        self.assertTrue(update_reorder_point(self.user_id, "Widget", 12))

        conn = sqlite3.connect(get_db_path())
        row = conn.execute(
            "SELECT reorder_point FROM inventory WHERE user_id = ? AND product = ?",
            (self.user_id, "Widget"),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 12)

    def test_expired_session_is_rejected_and_removed(self):
        conn = sqlite3.connect(get_db_path())
        conn.execute(
            "INSERT INTO users (id, username, password) VALUES (?, ?, ?)",
            (self.user_id, "student", "hash"),
        )
        conn.commit()
        conn.close()

        token = create_user_session(self.user_id)

        conn = sqlite3.connect(get_db_path())
        conn.execute("UPDATE user_sessions SET created_at = datetime('now', '-8 days')")
        conn.commit()
        conn.close()

        self.assertIsNone(get_user_by_session(token))

        conn = sqlite3.connect(get_db_path())
        session_count = conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
        conn.close()
        self.assertEqual(session_count, 0)

    def test_first_user_becomes_admin(self):
        self.assertTrue(register_user("admin", "password1"))
        admin_id = verify_user("admin", "password1")

        profile = get_user_profile(admin_id)

        self.assertTrue(profile["is_admin"])
        self.assertFalse(profile["must_change_password"])

    def test_new_user_starts_with_no_business_data(self):
        self.assertTrue(register_user("admin", "password1"))
        self.assertTrue(register_user("staff", "password1"))
        staff_id = verify_user("staff", "password1")

        conn = sqlite3.connect(get_db_path())
        inventory_count = conn.execute(
            "SELECT COUNT(*) FROM inventory WHERE user_id = ?",
            (staff_id,),
        ).fetchone()[0]
        sales_count = conn.execute(
            "SELECT COUNT(*) FROM sales WHERE user_id = ?",
            (staff_id,),
        ).fetchone()[0]
        conn.close()

        self.assertEqual(inventory_count, 0)
        self.assertEqual(sales_count, 0)

    def test_admin_can_reset_password_and_user_changes_it(self):
        register_user("admin", "password1")
        admin_id = verify_user("admin", "password1")
        register_user("staff", "oldpass1")
        staff_id = verify_user("staff", "oldpass1")
        create_user_session(staff_id)

        self.assertTrue(reset_user_password(admin_id, staff_id, "temppass1"))
        self.assertIsNone(verify_user("staff", "oldpass1"))
        self.assertEqual(verify_user("staff", "temppass1"), staff_id)
        self.assertTrue(get_user_profile(staff_id)["must_change_password"])

        conn = sqlite3.connect(get_db_path())
        session_count = conn.execute(
            "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?",
            (staff_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(session_count, 0)

        self.assertTrue(change_user_password(staff_id, "temppass1", "newpass1"))
        self.assertEqual(verify_user("staff", "newpass1"), staff_id)
        self.assertFalse(get_user_profile(staff_id)["must_change_password"])

    def test_admin_permissions_require_admin_and_keep_one_admin(self):
        register_user("admin", "password1")
        admin_id = verify_user("admin", "password1")
        register_user("staff", "oldpass1")
        staff_id = verify_user("staff", "oldpass1")

        self.assertFalse(set_user_admin(staff_id, staff_id, True))
        self.assertFalse(set_user_admin(admin_id, admin_id, False))
        self.assertTrue(set_user_admin(admin_id, staff_id, True))
        self.assertTrue(get_user_profile(staff_id)["is_admin"])

        users = list_users()
        self.assertEqual(len(users), 2)
        self.assertIn("active_sessions", users[0])

    def test_connect_db_uses_multi_user_friendly_pragmas(self):
        conn = connect_db()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()

        self.assertIn(journal_mode, {"wal", "memory"})
        self.assertGreaterEqual(busy_timeout, 30000)
        self.assertEqual(foreign_keys, 1)


if __name__ == "__main__":
    unittest.main()
