import pandas as pd
import os
import sqlite3
from statsmodels.tsa.arima.model import ARIMA

# --- SETUP PATHS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'inventory_system.db')

def load_and_prep_data(df, product_name):
    """Processes a DataFrame for live SQL integration."""
    product_df = df[df['product'] == product_name].copy()
    product_df['date'] = pd.to_datetime(product_df['date'])
    product_df.set_index('date', inplace=True)
    # Resample to Daily and fill gaps with 0
    return product_df['quantity'].resample('D').sum().fillna(0)

def run_forecast(series, steps=7):
    """Runs the ARIMA(1,1,1) model."""
    try:
        # ARIMA requires at least a few data points to converge
        if len(series) < 3:
            return pd.Series([series.mean()] * steps)
            
        model = ARIMA(series, order=(1, 1, 1))
        model_fit = model.fit()
        return model_fit.forecast(steps=steps)
    except Exception as e:
        print(f"Mathematical Error in ARIMA: {e}")
        return pd.Series([0] * steps)

def run_inventory_check(user_id):
    """The Main Engine: Pulls user-specific SQL data and saves a private forecast."""
    print(f"!!! ENGINE STARTING: USER {user_id} MODE !!!")
    
    # Create a user-specific output path
    output_path = os.path.join(BASE_DIR, 'data', f'forecast_user_{user_id}.csv')
    
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # CRITICAL: Filter by user_id so users don't see each other's trends
        query = f"SELECT product, date, quantity FROM sales WHERE user_id = {user_id}"
        sales_df = pd.read_sql(query, conn)
        
        if sales_df.empty:
            print(f"No data found for user {user_id}")
            conn.close()
            return False
            
        all_products = sales_df['product'].unique()
    except Exception as e:
        print(f"DB Error: {e}")
        conn.close()
        return False

    export_rows = [] 
    
    for product in all_products:
        ts_data = load_and_prep_data(sales_df, product)
        predictions = run_forecast(ts_data)
        
        # Determine starting date for forecast labels
        last_date = ts_data.index[-1]
        
        for i, value in enumerate(predictions, start=1):
            forecast_date = last_date + pd.Timedelta(days=i)
            export_rows.append({
                'product': product,
                'forecast_date': forecast_date.date(),
                'predicted_quantity': max(0, round(value, 2)) # Prevent negative demand
            })
            
    if export_rows:
        results_df = pd.DataFrame(export_rows)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        results_df.to_csv(output_path, index=False)
        print(f"--- SUCCESS: User {user_id} forecast saved ---")
        conn.close()
        return True
    
    conn.close()
    return False