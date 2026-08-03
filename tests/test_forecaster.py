import datetime as dt
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

import pandas as pd

import models.forecaster as forecaster
from models.database_manager import (
    add_sales_record,
    get_db_path,
    init_db,
    mark_forecast_cache_stale,
)


class ForecasterCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        self.old_db_path = os.environ.get("INVENTORY_DB_PATH")
        os.chdir(self.temp_dir.name)
        os.environ["INVENTORY_DB_PATH"] = os.path.join(
            self.temp_dir.name, "inventory_system.db"
        )
        init_db()
        self.output_dir = os.path.join(self.temp_dir.name, "data")
        os.makedirs(self.output_dir, exist_ok=True)
        self.output_patch = mock.patch.object(
            forecaster,
            "get_data_path",
            side_effect=lambda filename: os.path.join(self.output_dir, filename),
        )
        self.output_patch.start()

        conn = sqlite3.connect(get_db_path())
        rows = []
        for product in ("Widget", "Gadget"):
            for day in range(1, 11):
                rows.append((1, product, f"2026-01-{day:02d}", day))
        conn.executemany(
            "INSERT INTO sales (user_id, product, date, quantity) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.output_patch.stop()
        os.chdir(self.old_cwd)
        if self.old_db_path is None:
            os.environ.pop("INVENTORY_DB_PATH", None)
        else:
            os.environ["INVENTORY_DB_PATH"] = self.old_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def fake_forecast(series, steps=7):
        return pd.Series([float(series.mean())] * steps), "Success"

    @staticmethod
    def fake_evaluation(series):
        return {"mape": 10.0, "mae": 1.0, "mse": 1.0, "rmse": 1.0, "mase": 0.5}

    def read_cache(self):
        conn = sqlite3.connect(get_db_path())
        rows = conn.execute(
            "SELECT product_id, stale, data_signature FROM forecast_cache "
            "WHERE user_id = ? ORDER BY product_id",
            (1,),
        ).fetchall()
        conn.close()
        return rows

    def test_forecast_cache_is_reused_without_retraining(self):
        with mock.patch.object(forecaster, "run_forecast", side_effect=self.fake_forecast) as run_mock, \
             mock.patch.object(forecaster, "evaluate_forecast", side_effect=self.fake_evaluation):
            self.assertTrue(forecaster.run_inventory_check(1))
            self.assertTrue(forecaster.run_inventory_check(1))

        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(len(self.read_cache()), 2)
        self.assertTrue(all(row[1] == 0 for row in self.read_cache()))

    def test_sales_change_marks_only_that_product_stale(self):
        with mock.patch.object(forecaster, "run_forecast", side_effect=self.fake_forecast), \
             mock.patch.object(forecaster, "evaluate_forecast", side_effect=self.fake_evaluation):
            forecaster.run_inventory_check(1)

        mark_forecast_cache_stale(1, ["Widget"])
        cache = {row[0]: row[1] for row in self.read_cache()}
        self.assertEqual(cache["Widget"], 1)
        self.assertEqual(cache["Gadget"], 0)

    def test_changed_products_retrain_only_selected_product(self):
        with mock.patch.object(forecaster, "run_forecast", side_effect=self.fake_forecast) as run_mock, \
             mock.patch.object(forecaster, "evaluate_forecast", side_effect=self.fake_evaluation):
            forecaster.run_inventory_check(1)
            run_mock.reset_mock()
            forecaster.run_inventory_check(1, force_refresh=True, changed_products=["Widget"])

        self.assertEqual(run_mock.call_count, 1)

    def test_background_refresh_processes_change_queued_during_active_worker(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_check(user_id, force_refresh=False, changed_products=None):
            calls.append(changed_products)
            if len(calls) == 1:
                started.set()
                release.wait(timeout=5)
            return True

        with mock.patch.object(forecaster, "run_inventory_check", side_effect=slow_check):
            self.assertTrue(forecaster.start_background_inventory_check(1, ["Widget"]))
            self.assertTrue(started.wait(timeout=5))
            self.assertFalse(forecaster.start_background_inventory_check(1, ["Gadget"]))
            release.set()

            deadline = time.time() + 5
            while time.time() < deadline and len(calls) < 2:
                time.sleep(0.01)

        self.assertEqual(calls, [["Widget"], ["Gadget"]])


if __name__ == "__main__":
    unittest.main()
