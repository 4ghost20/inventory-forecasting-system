# inventory-forecasting-system
A Web-Based Inventory Demand Forecasting System for Small Retail Enterprises project

## Features

- User registration and login with hashed passwords
- Per-user inventory and sales records stored in SQLite
- Stock updates for new sales, restocking, and deleted sale corrections
- 7-day demand forecasting with ARIMA and fallback handling
- Forecast evaluation using MAE, MSE, RMSE, MAPE, and MASE
- Stock-gap analysis and purchase suggestions

## Run the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Run checks

```bash
python -m py_compile app.py main.py data_handler.py models/database_manager.py models/forecaster.py models/analyzer.py
python -m unittest discover -s tests
```
