import pandas as pd
import os
import sqlite3
import logging
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
import numpy as np

# --- SETUP PATHS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'inventory_system.db')

# Setup logging to track forecast runs
log_path = os.path.join(BASE_DIR, 'data', 'forecast_log.txt')
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
        return 0
    return np.mean(np.abs((actual[non_zero] - predicted[non_zero]) / actual[non_zero])) * 100

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

def run_inventory_check(user_id):
    """The Main Engine: Pulls user-specific SQL data and saves a private forecast."""
    print(f"!!! ENGINE STARTING: USER {user_id} MODE !!!")
    logging.info(f"Forecast started for user {user_id}")
    
    #user-specific output path
    output_path = os.path.join(BASE_DIR, 'data', f'forecast_user_{user_id}.csv')
    metrics_path = os.path.join(BASE_DIR, 'data', f'forecast_metrics_user_{user_id}.csv')
    
    # Check if recent forecast exists (cache for 1 hour)
    if os.path.exists(output_path):
        file_time = os.path.getmtime(output_path)
        current_time = pd.Timestamp.now().timestamp()
        if current_time - file_time < 3600:  # 1 hour
            print(f"Using cached forecast for user {user_id}")
            logging.info(f"Using cached forecast for user {user_id}")
            return True
    
    conn = sqlite3.connect(DB_PATH)
    
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
            
            # Calculate accuracy metric
            if len(ts_data) >= 7:
                # Use last 7 points as test, rest as train
                mape_score = calculate_mape(ts_data.tail(7).values, predictions[:7].values)
            else:
                mape_score = 0
            
            metrics_data.append({
                'product': product,
                'data_points': len(ts_data),
                'forecast_status': status,
                'accuracy': f"{100 - mape_score:.1f}%"
            })
            
            # Determine starting date for forecast labels
            last_date = ts_data.index[-1]
            
            for i, value in enumerate(predictions, start=1):
                forecast_date = last_date + pd.Timedelta(days=i)
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
                'accuracy': 'N/A'
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