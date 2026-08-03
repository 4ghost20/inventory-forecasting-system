import pandas as pd
import os
import logging
import json
import threading
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
import numpy as np
from models.database_manager import connect_db, get_data_path

# Setup logging to track forecast runs
log_path = get_data_path('forecast_log.txt')
logging.basicConfig(filename=log_path, level=logging.INFO, 
                   format='%(asctime)s - %(message)s')

_forecast_threads = {}
_pending_forecast_changes = {}
_forecast_threads_lock = threading.Lock()
_NO_PENDING_REFRESH = object()

def load_and_prep_data(df, product_name):
    """Processes a DataFrame for live SQL integration."""
    product_df = df[df['product'] == product_name].copy()
    return load_and_prep_product_data(product_df)

def load_and_prep_product_data(product_df):
    """Build a daily demand series for one product."""
    product_df = product_df.copy()
    product_df['date'] = pd.to_datetime(product_df['date'])
    product_df.set_index('date', inplace=True)
    # Resample to Daily and fill gaps with 0
    series = product_df['quantity'].resample('D').sum().fillna(0)
    # Remove outliers
    series = detect_outliers(series)
    return series

def detect_outliers(series):
    # Find outliers using IQR method
    Q1 = series.quantile(0.25)
    Q3 = series.quantile(0.75)
    IQR = Q3 - Q1
    lower_bound = Q1 - 1.5 * IQR
    upper_bound = Q3 + 1.5 * IQR
    # Replace outliers with median
    median = series.median()
    series = series.where((series >= lower_bound) & (series <= upper_bound), median)
    return series

def check_stationarity(series):
    """Check if data is stationary using ADF test (needed for ARIMA)."""
    try:
        adf_result = adfuller(series, autolag='AIC')
        # If p-value < 0.05, data is stationary
        return adf_result[1] < 0.05
    except:
        return False  # If test fails, assume not stationary

def calculate_mape(actual, predicted):
    """Calculate Mean Absolute Percentage Error (accuracy metric)."""
    # MAPE shows forecast accuracy as percentage
    actual = np.array(actual)
    predicted = np.array(predicted)
    non_zero = actual != 0
    if non_zero.sum() == 0:
        return None
    return np.mean(np.abs((actual[non_zero] - predicted[non_zero]) / actual[non_zero])) * 100

def calculate_mae(actual, predicted):
    """Calculate Mean Absolute Error."""
    actual = np.array(actual)
    predicted = np.array(predicted)
    return np.mean(np.abs(actual - predicted))

def calculate_rmse(actual, predicted):
    """Calculate Root Mean Square Error."""
    actual = np.array(actual)
    predicted = np.array(predicted)
    return np.sqrt(np.mean((actual - predicted) ** 2))

def calculate_mse(actual, predicted):
    """Calculate Mean Squared Error."""
    actual = np.array(actual)
    predicted = np.array(predicted)
    return np.mean((actual - predicted) ** 2)

def calculate_mase(actual, predicted, training_series):
    """Calculate Mean Absolute Scaled Error against a naive one-step forecast."""
    training_series = pd.Series(training_series).dropna()
    if len(training_series) < 2:
        return None

    naive_error = training_series.diff().abs().dropna().mean()
    forecast_mae = calculate_mae(actual, predicted)

    if naive_error == 0:
        return 0 if forecast_mae == 0 else None

    return forecast_mae / naive_error

def evaluate_forecast(series):
    """Backtest the model on a recent holdout window and return forecast metrics."""
    if len(series) < 8:
        return {
            'mape': None,
            'mae': None,
            'mse': None,
            'rmse': None,
            'mase': None
        }

    test_size = min(7, len(series) - 7)
    train = series.iloc[:-test_size]
    test = series.iloc[-test_size:]

    predicted, _ = run_forecast(train, steps=test_size)
    predicted = pd.Series(predicted).iloc[:test_size].values
    actual = test.values

    return {
        'mape': calculate_mape(actual, predicted),
        'mae': calculate_mae(actual, predicted),
        'mse': calculate_mse(actual, predicted),
        'rmse': calculate_rmse(actual, predicted),
        'mase': calculate_mase(actual, predicted, train)
    }

def format_metric(value, decimals=2):
    """Format optional metric values for CSV display."""
    if value is None or pd.isna(value):
        return 'N/A'
    return f"{value:.{decimals}f}"

def metric_to_db(value):
    """Store optional metrics as real numbers in SQLite."""
    if value is None or pd.isna(value):
        return None
    return float(value)

def build_sales_signature(product_df):
    """Create a compact product-level signature to detect sales changes."""
    if product_df.empty:
        return "0|0||0"

    quantity_sum = pd.to_numeric(product_df['quantity'], errors='coerce').fillna(0).sum()
    latest_date = pd.to_datetime(product_df['date'], errors='coerce').max()
    latest_date_text = '' if pd.isna(latest_date) else latest_date.strftime('%Y-%m-%d')
    return f"{len(product_df)}|{int(product_df['id'].max())}|{latest_date_text}|{float(quantity_sum):.2f}"

def cache_row_is_valid(row, signature):
    """Return True when a cached forecast still matches the product's sales data."""
    return (
        row is not None
        and int(row.get('stale', 0) or 0) == 0
        and row.get('data_signature') == signature
        and bool(row.get('forecast_json'))
    )

def cached_outputs_from_row(product, row):
    """Convert one SQLite cache row into CSV-compatible forecast and metric rows."""
    forecast_rows = json.loads(row['forecast_json'])
    metrics_row = {
        'product': product,
        'data_points': int(row.get('data_points') or 0),
        'forecast_status': row.get('forecast_status') or 'Success',
        'accuracy': row.get('accuracy_label') or 'N/A',
        'mae': format_metric(row.get('mae')),
        'mse': format_metric(row.get('mse')),
        'rmse': format_metric(row.get('rmse')),
        'mape': format_metric(row.get('mape')),
        'mase': format_metric(row.get('mase'))
    }
    return forecast_rows, metrics_row

def save_product_forecast_cache(conn, user_id, product, forecast_rows, metrics_row, evaluation, signature):
    """Upsert one product forecast into SQLite cache."""
    conn.execute(
        '''INSERT INTO forecast_cache
           (user_id, product_id, forecast_json, mae, mse, rmse, mape, mase,
            accuracy_label, forecast_status, data_points, data_signature, stale, last_trained)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))
           ON CONFLICT(user_id, product_id) DO UPDATE SET
             forecast_json = excluded.forecast_json,
             mae = excluded.mae,
             mse = excluded.mse,
             rmse = excluded.rmse,
             mape = excluded.mape,
             mase = excluded.mase,
             accuracy_label = excluded.accuracy_label,
             forecast_status = excluded.forecast_status,
             data_points = excluded.data_points,
             data_signature = excluded.data_signature,
             stale = 0,
             last_trained = datetime('now')''',
        (
            user_id,
            product,
            json.dumps(forecast_rows),
            metric_to_db(evaluation['mae']),
            metric_to_db(evaluation['mse']),
            metric_to_db(evaluation['rmse']),
            metric_to_db(evaluation['mape']),
            metric_to_db(evaluation['mase']),
            metrics_row['accuracy'],
            metrics_row['forecast_status'],
            metrics_row['data_points'],
            signature
        )
    )

def run_forecast(series, steps=7):
    """Runs the ARIMA(1,1,1) model with error handling."""
    try:
        # Check if we have enough data points
        if len(series) < 7:
            warning = f"Insufficient data points ({len(series)} days). Need at least 7 days for reliable forecast. Using simple average."
            logging.warning(warning)
            return pd.Series([series.mean()] * steps), warning
        
        # Check if data is stationary for ARIMA
        is_stationary = check_stationarity(series)
        status = "OK (stationary)" if is_stationary else "Warning (non-stationary)"
        logging.info(f"Data stationarity: {status}")
        
        # Fit ARIMA model
        model = ARIMA(series, order=(1, 1, 1))
        model_fit = model.fit()
        forecast_result = model_fit.forecast(steps=steps)
        
        return forecast_result, "Success"
        
    except Exception as e:
        # If ARIMA fails, use simple average as fallback
        warning = f"ARIMA fitting failed: {str(e)}. Using fallback (average)."
        logging.error(warning)
        return pd.Series([series.mean()] * steps), warning

def run_inventory_check(user_id, force_refresh=False, changed_products=None):
    """The Main Engine: Pulls user-specific SQL data and saves a private forecast."""
    print(f"!!! ENGINE STARTING: USER {user_id} MODE !!!")
    logging.info(f"Forecast started for user {user_id}")
    
    #user-specific output path
    output_path = get_data_path(f'forecast_user_{user_id}.csv')
    metrics_path = get_data_path(f'forecast_metrics_user_{user_id}.csv')
    changed_products = None if changed_products is None else {
        str(product).strip() for product in changed_products if str(product).strip()
    }
    conn = connect_db()
    
    try:
        # CRITICAL: Filter by user_id so users don't see each other's trends
        query = "SELECT id, product, date, quantity FROM sales WHERE user_id = ?"
        sales_df = pd.read_sql(query, conn, params=(user_id,))
        
        if sales_df.empty:
            print(f"No sales data found for user {user_id}")
            logging.warning(f"No sales data for user {user_id}")
            conn.close()
            return False
            
        all_products = sorted(sales_df['product'].dropna().astype(str).unique())
        cache_df = pd.read_sql(
            '''SELECT product_id, forecast_json, mae, mse, rmse, mape, mase,
                      accuracy_label, forecast_status, data_points, data_signature, stale
               FROM forecast_cache
               WHERE user_id = ?''',
            conn,
            params=(user_id,)
        )
        cache_rows = {
            row['product_id']: row
            for _, row in cache_df.iterrows()
        }
    except Exception as e:
        print(f"Error loading data: {e}")
        logging.error(f"Error loading data: {e}")
        conn.close()
        return False

    export_rows = [] 
    metrics_data = []
    retrained_count = 0
    cached_count = 0
    
    for product in all_products:
        try:
            product_sales = sales_df[sales_df['product'] == product].copy()
            signature = build_sales_signature(product_sales)
            cache_row = cache_rows.get(product)
            should_refresh = force_refresh and (changed_products is None or product in changed_products)

            if not should_refresh and cache_row_is_valid(cache_row, signature):
                cached_forecast_rows, cached_metrics_row = cached_outputs_from_row(product, cache_row)
                export_rows.extend(cached_forecast_rows)
                metrics_data.append(cached_metrics_row)
                cached_count += 1
                continue

            ts_data = load_and_prep_product_data(product_sales)
            predictions, status = run_forecast(ts_data)
            
            # Calculate accuracy metrics with a recent holdout backtest.
            evaluation = evaluate_forecast(ts_data)
            mape_score = evaluation['mape']
            accuracy = 'N/A' if mape_score is None else f"{max(0, 100 - mape_score):.1f}%"
            
            metrics_data.append({
                'product': product,
                'data_points': len(ts_data),
                'forecast_status': status,
                'accuracy': accuracy,
                'mae': format_metric(evaluation['mae']),
                'mse': format_metric(evaluation['mse']),
                'rmse': format_metric(evaluation['rmse']),
                'mape': format_metric(evaluation['mape']),
                'mase': format_metric(evaluation['mase'])
            })
            
            # Forecast labels should represent the upcoming days from today.
            start_date = max(ts_data.index[-1].date(), pd.Timestamp.today().date())
            
            for i, value in enumerate(predictions, start=1):
                forecast_date = pd.Timestamp(start_date) + pd.Timedelta(days=i)
                export_rows.append({
                    'product': product,
                    'forecast_date': str(forecast_date.date()),
                    'predicted_quantity': max(0, round(value, 2)) # Prevent negative demand
                })
            save_product_forecast_cache(
                conn,
                user_id,
                product,
                export_rows[-7:],
                metrics_data[-1],
                evaluation,
                signature
            )
            retrained_count += 1
        except Exception as e:
            print(f"Error forecasting for {product}: {e}")
            logging.error(f"Error forecasting for {product}: {e}")
            metrics_data.append({
                'product': product,
                'data_points': 0,
                'forecast_status': 'Failed',
                'accuracy': 'N/A',
                'mae': 'N/A',
                'mse': 'N/A',
                'rmse': 'N/A',
                'mape': 'N/A',
                'mase': 'N/A'
            })
            continue  # Skip this product
            
    if export_rows:
        try:
            conn.commit()
            results_df = pd.DataFrame(export_rows)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            results_df.to_csv(output_path, index=False)
            
            # Save metrics to file
            metrics_df = pd.DataFrame(metrics_data)
            metrics_df.to_csv(metrics_path, index=False)
            
            print(f"--- SUCCESS: User {user_id} forecast saved ---")
            logging.info(
                f"Forecast successfully saved for user {user_id}. "
                f"Retrained {retrained_count}, reused {cached_count} cached products."
            )
            conn.close()
            return True
        except Exception as e:
            conn.rollback()
            print(f"Error saving forecast: {e}")
            conn.close()
            return False
    
    conn.close()
    return False

def _merge_changed_products(existing, incoming):
    """Combine refresh requests while preserving a full-refresh request."""
    if existing is None or incoming is None:
        return None
    return sorted(set(existing).union(incoming))


def _run_background_forecast(user_id, changed_products):
    """Run one worker and immediately process a refresh queued during it."""
    try:
        run_inventory_check(
            user_id=user_id,
            force_refresh=True,
            changed_products=changed_products
        )
    finally:
        with _forecast_threads_lock:
            pending = _pending_forecast_changes.pop(user_id, _NO_PENDING_REFRESH)
            if pending is _NO_PENDING_REFRESH:
                _forecast_threads.pop(user_id, None)
            else:
                next_thread = threading.Thread(
                    target=_run_background_forecast,
                    args=(user_id, pending),
                    daemon=True
                )
                _forecast_threads[user_id] = next_thread
                next_thread.start()


def start_background_inventory_check(user_id, changed_products=None):
    """Run forecast regeneration in a daemon thread and queue overlapping changes."""
    if changed_products is None:
        normalized_products = None
    else:
        normalized_products = sorted({
            str(product).strip()
            for product in changed_products
            if str(product).strip()
        })

    with _forecast_threads_lock:
        existing = _forecast_threads.get(user_id)
        if existing and existing.is_alive():
            queued = _pending_forecast_changes.get(user_id, [])
            _pending_forecast_changes[user_id] = _merge_changed_products(
                queued, normalized_products
            )
            return False

        thread = threading.Thread(
            target=_run_background_forecast,
            args=(user_id, normalized_products),
            daemon=True
        )
        _forecast_threads[user_id] = thread
        thread.start()
        return True
