# inventory-forecasting-system
A Web-Based Inventory Demand Forecasting System for Small Retail Enterprises project

## Features

- User registration and login with hashed passwords
- Browser sessions with automatic expiry after 7 days
- First user becomes an admin automatically
- Admin user management for password resets, admin permissions, and session revocation
- Logged-in users can change their own password
- Per-user inventory and sales records stored in SQLite
- New users start with empty inventory and sales data
- Stock updates for new sales, restocking, and deleted sale corrections
- CSV/Excel import for historical sales data
- Duplicate sales are skipped during import to reduce accidental double-counting
- 7-day demand forecasting with ARIMA and fallback handling
- Forecast evaluation using MAE, MSE, RMSE, MAPE, and MASE
- Stock-gap analysis and purchase suggestions
- Reorder points can be updated from the records page

## Run the app

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## Run checks

```bash
python -m py_compile app.py main.py data_handler.py models/database_manager.py models/forecaster.py models/analyzer.py
python -m unittest discover -s tests
```

## Database location

By default, the app stores data in:

```text
inventory_system.db
```

For testing or deployment, set `INVENTORY_DB_PATH` to use another SQLite file without changing code.

## Admin access

The first account created in a new database is made admin automatically. In an existing database, the first user is promoted to admin if no admin exists yet.

Admins can:

- Reset another user's password
- Require a user to change password after reset
- Grant or remove admin access
- Revoke a user's active sessions

## Import file format

CSV or Excel files must include these columns:

```text
date, product, quantity
```

Optional inventory columns:

```text
current_stock, reorder_point
```

The importer also accepts common names such as `Date`, `Product Name`, `Qty Sold`, and `Opening Stock`.
