import pandas as pd
import os
import matplotlib.pyplot as plt
from statsmodels.tsa.arima.model import ARIMA

# --- REUSABLE MODULES ---

def load_and_prep_data(file_path, product_name):
    """Cleans and resamples data for a specific product."""
    df = pd.read_csv(file_path)
    product_df = df[df['product'] == product_name].copy()
    product_df['date'] = pd.to_datetime(product_df['date'])
    product_df.set_index('date', inplace=True)
    return product_df['quantity'].resample('D').sum().fillna(0)

def run_forecast(series, steps=7):
    """Runs the ARIMA(1,1,1) model."""
    model = ARIMA(series, order=(1, 1, 1))
    model_fit = model.fit()
    return model_fit.forecast(steps=steps)

# --- DAY 7 MASTER FUNCTION ---

def forecast_product(product_name):
    """
    The main entry point to forecast any product.
    This will be the core of our Week 2 multi-product loop.
    """
    print(f"\n--- Starting Forecast for: {product_name} ---")
    
    # Path to data
    data_path = os.path.join('data', 'sample_data.csv')
    
    # Step 1: Prepare
    ts_data = load_and_prep_data(data_path, product_name)
    
    # Step 2: Forecast
    predictions = run_forecast(ts_data)
    
    # Step 3: Print results (for now)
    print(f"Predicted demand for next 7 days:")
    print(predictions)
    
    return predictions

if __name__ == "__main__":
    # Now you can forecast ANY product by just changing this name!
    forecast_product('Widget_A')
    forecast_product('Gadget_B')