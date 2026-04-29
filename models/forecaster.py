import pandas as pd
import os
from statsmodels.tsa.arima.model import ARIMA

print("!!! SCRIPT IS TRIGGERED !!!")

# --- REUSABLE MODULES ---

def load_and_prep_data(file_path, product_name):
    df = pd.read_csv(file_path)
    product_df = df[df['product'] == product_name].copy()
    product_df['date'] = pd.to_datetime(product_df['date'])
    product_df.set_index('date', inplace=True)
    return product_df['quantity'].resample('D').sum().fillna(0)

def run_forecast(series, steps=7):
    model = ARIMA(series, order=(1, 1, 1))
    model_fit = model.fit()
    return model_fit.forecast(steps=steps)

def forecast_product(product_name, input_path):
    """Now accepts input_path to ensure absolute pathing."""
    print(f"\n--- Starting Forecast for: {product_name} ---")
    ts_data = load_and_prep_data(input_path, product_name)
    predictions = run_forecast(ts_data)
    print(f"Predicted demand for next 7 days:\n{predictions}")
    return predictions

def run_inventory_check():
    # 1. Setup Absolute Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_folder = os.path.join(project_root, 'data')
    input_path = os.path.join(data_folder, 'sample_data.csv')
    output_path = os.path.join(data_folder, 'forecast_results.csv')

    # 2. THE PERMISSION FIX: Force create folder with explicit check
    if not os.path.exists(data_folder):
        try:
            os.makedirs(data_folder, exist_ok=True)
            print(f"Verified/Created folder: {data_folder}")
        except Exception as e:
            print(f"CRITICAL PERMISSION ERROR: Could not create folder. {e}")
            return

    # 3. Load and Process
    df = pd.read_csv(input_path)
    all_products = df['product'].unique()
    export_rows = [] 
    
    print(f"Starting batch forecast for {len(all_products)} products...")
    
    for product in all_products:
        predictions = forecast_product(product, input_path)
        for date, value in predictions.items():
            export_rows.append({
                'product': product,
                'forecast_date': date,
                'predicted_quantity': round(value, 2)
            })
            
    # 4. Save
    results_df = pd.DataFrame(export_rows)
    results_df.to_csv(output_path, index=False)
    print(f"\n--- SUCCESS: Forecast results saved to {output_path} ---")
    return results_df

# --- EXECUTION ---
print("!!! ENGINE STARTING !!!")
run_inventory_check()
print("!!! ENGINE FINISHED !!!")