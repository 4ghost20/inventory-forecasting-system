# Inventory Forecasting System User Guide

This guide explains how to run the application, use its main features, and locate the code responsible for each part of the system.

## 1. Running The Application

Install the dependencies from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

Start Streamlit from the project folder:

```powershell
streamlit run app.py
```

The application creates or updates the SQLite database automatically when it starts. The default database is `inventory_system.db`.

## 2. First Use

1. Register a user account.
2. Log in with the account.
3. Open `Add Data`.
4. Add an inventory item manually or import sales data.
5. Run the forecast from the sidebar.
6. Open `Dashboard` to view the seven-day forecast and metrics.
7. Use `Analyze Stock Gaps` to create purchase suggestions.

Each user has separate inventory, sales, forecasts, and sessions.

## 3. Importing CSV Or Excel Data

The preferred required columns are:

| Required column | Meaning |
| --- | --- |
| `date` | Date of the sale |
| `product` | Product name |
| `quantity` | Units sold |

Optional columns are `current_stock` and `reorder_point`.

The import screen accepts common alternative names. For example:

| Dataset column | Converted to |
| --- | --- |
| `InvoiceDate`, `invoice_date`, `sale_date` | `date` |
| `Description`, `Item`, `item_name`, `product_name` | `product` |
| `Qty`, `qty_sold`, `quantity_sold`, `units_sold` | `quantity` |
| `Stock`, `opening_stock`, `available_stock` | `current_stock` |
| `Reorder`, `minimum_stock` | `reorder_point` |

Column names are converted to lowercase, spaces become underscores, and aliases are applied before validation. This is why a real dataset can use names such as `InvoiceDate` and `Description` without changing the database design.

During import, invalid, future-dated, zero-quantity, and duplicate rows are skipped. Valid rows are cleaned first and inserted with one SQLite batch transaction. If the transaction fails, it is rolled back.

## 4. Main Pages And Controls

### Dashboard

The dashboard displays the selected product's current stock, seven-day forecast, stock gap, chart, forecast metrics, and a CSV download button.

### Add Data

This page supports:

- bulk CSV and Excel imports
- manual sale entry
- stock restocking
- registering a new product

After a data change, the affected product's forecast cache is marked stale. Forecast regeneration runs in the background so the import or update can finish promptly.

### Database View

This page supports:

- deleting an individual sale
- restoring stock after a sale is deleted
- purging a product and its sales history
- changing a product's reorder point
- viewing inventory and sales records

### Account

Users can change their passwords. Administrators can manage users and reset passwords.

## 5. Forecasting Workflow

The forecasting flow is:

1. Sales are loaded for the logged-in user only.
2. Sales are grouped by product and resampled into daily demand.
3. Missing days are filled with zero demand.
4. Outliers are replaced using the IQR method.
5. The existing fixed `ARIMA(1,1,1)` model generates a seven-day forecast.
6. If there is insufficient history or ARIMA fails, the system uses average demand.
7. The system calculates MAE, MSE, RMSE, MAPE, and MASE.
8. Forecast rows and metrics are stored in SQLite and exported to CSV for the current dashboard and analysis code.

Automatic ARIMA selection, confidence intervals, SARIMA, and LSTM are not part of the current implementation.

## 6. Forecast Cache And Performance

The `forecast_cache` table stores each user's forecast and metrics for each product. A compact sales signature records the number of sales, latest sale ID, latest date, and total quantity.

When a forecast is requested:

- a matching, non-stale cache row is reused;
- a changed product is retrained;
- unchanged products are not retrained;
- the CSV files are regenerated from the cached and newly trained results.

When a sale is added, deleted, imported, or when relevant inventory data changes, only the affected product is marked stale. If another change arrives while a background forecast is running, it is queued and processed after the current worker finishes.

The first forecast for a product still requires model training. Later requests are faster because they use the SQLite cache.

## 7. Database And Migration

Database initialization is in `models/database_manager.py`, inside `init_db()`.

On startup it creates the `forecast_cache` table and these indexes when they do not already exist:

- `idx_sales_user_product`
- `idx_sales_date`
- `idx_sales_user_product_date`
- `idx_inventory_user_product`
- `idx_forecast_cache_user_product`

No manual SQL migration is required for a normal existing installation. Start the application once and `init_db()` applies the missing table and indexes.

## 8. Code Map

### User interface: `app.py`

- `standardize_import_columns()` normalizes uploaded column names.
- `clear_user_forecast_cache()` marks cache rows stale and removes old CSV exports.
- `init_db()` is called during application startup.
- The `Dashboard` section displays forecasts and metrics.
- The `Add Data` section handles imports, sales, restocking, and new products.
- The `Database View` section handles deletes, product purging, and reorder points.
- Sidebar buttons call `run_inventory_check()` and `run_gap_analysis()`.

### Database and business operations: `models/database_manager.py`

- `connect_db()` opens SQLite with foreign keys, a busy timeout, and WAL mode.
- `init_db()` creates tables, indexes, and small schema migrations.
- `register_user()`, `verify_user()`, and session functions implement authentication.
- `add_sales_record()` records a sale and deducts stock transactionally.
- `update_stock_level()` handles restocking.
- `add_new_inventory_item()` registers products.
- `bulk_import_sales()` validates data and performs batch imports.
- `delete_transaction()` deletes a sale and restores its stock.
- `delete_product_fully()` removes a product and its sales history.
- `update_reorder_point()` changes reorder settings.
- `mark_forecast_cache_stale()` invalidates affected forecasts.
- `delete_forecast_cache_for_products()` removes cache rows for deleted products.

### Forecasting: `models/forecaster.py`

- `load_and_prep_product_data()` creates the daily demand series.
- `detect_outliers()` replaces extreme values.
- `run_forecast()` runs ARIMA or the average-demand fallback.
- `evaluate_forecast()` calculates forecast quality metrics.
- `build_sales_signature()` detects whether product sales changed.
- `save_product_forecast_cache()` writes forecasts and metrics to SQLite.
- `run_inventory_check()` loads cache rows, retrains changed products, and writes CSV outputs.
- `start_background_inventory_check()` starts non-blocking forecast regeneration.

### Stock-gap analysis: `models/analyzer.py`

- `run_gap_analysis()` compares current stock with seven-day demand.
- `export_action_plan()` writes purchase suggestions to a user-specific text file.

### Tests: `tests/`

- `test_database_manager.py` tests authentication, inventory, sales, imports, sessions, migration objects, and indexes.
- `test_forecaster.py` tests cache reuse, invalidation, selective retraining, and background refresh queuing.

## 9. Generated Files

The application writes user-specific forecast exports under `data/`:

- `forecast_user_<id>.csv`
- `forecast_metrics_user_<id>.csv`
- `purchase_order_suggestions_user_<id>.txt`
- `forecast_log.txt`

These files are runtime outputs. The SQLite database remains the main source of stored application data and forecast cache data.

## 10. Useful Verification Commands

Run syntax checks:

```powershell
python -m py_compile app.py models\database_manager.py models\forecaster.py models\analyzer.py
```

Run all tests:

```powershell
python -m unittest discover -s tests
```
