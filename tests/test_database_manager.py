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
    delete_transaction,
    init_db,
)


class DatabaseManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)
        init_db()
        self.user_id = 1

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.temp_dir.cleanup()

    def read_stock(self, product):
        conn = sqlite3.connect("inventory_system.db")
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

        conn = sqlite3.connect("inventory_system.db")
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


if __name__ == "__main__":
    unittest.main()
