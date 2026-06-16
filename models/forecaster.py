import pandas as pd
import os
import logging
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
import numpy as np
from models.database_manager import connect_db, get_data_path

# Setup logging to track forecast runs
log_path = get_data_path('forecast_log.txt')
logging.basicConfig(filename=log_path, level=logging.INFO, 
                   format='%(asctime)s - %(message)s')

def load_and_prep_data(df, product_name):
    """Processes a DataFrame for live SQL integration."""
    product_df = df[df['product'] == product_name].copy()
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

def run_inventory_check(user_id, force_refresh=False):
    """The Main Engine: Pulls user-specific SQL data and saves a private forecast."""
    print(f"!!! ENGINE STARTING: USER {user_id} MODE !!!")
    logging.info(f"Forecast started for user {user_id}")
    
    #user-specific output path
    output_path = get_data_path(f'forecast_user_{user_id}.csv')
    metrics_path = get_data_path(f'forecast_metrics_user_{user_id}.csv')
    
    # Check if recent forecast exists (cache for 1 hour)
    if os.path.exists(output_path) and not force_refresh:
        file_time = os.path.getmtime(output_path)
        current_time = pd.Timestamp.now().timestamp()
        metrics_are_current = False
        if os.path.exists(metrics_path):
            try:
                existing_metrics = pd.read_csv(metrics_path, nrows=1)
                metrics_are_current = {'mae', 'mse', 'rmse', 'mape'}.issubset(existing_metrics.columns)
            except Exception:
                metrics_are_current = False

        if current_time - file_time < 3600 and metrics_are_current:  # 1 hour
            print(f"Using cached forecast for user {user_id}")
            logging.info(f"Using cached forecast for user {user_id}")
            return True
    
    conn = connect_db()
    
    try:
        # CRITICAL: Filter by user_id so users don't see each other's trends
        query = "SELECT product, date, quantity FROM sales WHERE user_id = ?"
        sales_df = pd.read_sql(query, conn, params=(user_id,))
        
        if sales_df.empty:
            print(f"No sales data found for user {user_id}")
            logging.warning(f"No sales data for user {user_id}")
            conn.close()
            return False
            
        all_products = sales_df['product'].unique()
    except Exception as e:
        print(f"Error loading data: {e}")
        logging.error(f"Error loading data: {e}")
        conn.close()
        return False

    export_rows = [] 
    metrics_data = []
    
    for product in all_products:
        try:
            ts_data = load_and_prep_data(sales_df, product)
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
                    'forecast_date': forecast_date.date(),
                    'predicted_quantity': max(0, round(value, 2)) # Prevent negative demand
                })
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
            results_df = pd.DataFrame(export_rows)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            results_df.to_csv(output_path, index=False)
            
            # Save metrics to file
            metrics_df = pd.DataFrame(metrics_data)
            metrics_df.to_csv(metrics_path, index=False)
            
            print(f"--- SUCCESS: User {user_id} forecast saved ---")
            logging.info(f"Forecast successfully saved for user {user_id}")
            conn.close()
            return True
        except Exception as e:
            print(f"Error saving forecast: {e}")
            conn.close()
            return False
    
    conn.close()
    return False
