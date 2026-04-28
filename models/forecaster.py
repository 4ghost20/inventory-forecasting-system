import pandas as pd
import os
import matplotlib.pyplot as plt # Day 6 requirement
from statsmodels.tsa.arima.model import ARIMA

def load_and_prep_data(file_path, product_name):
    df = pd.read_csv(file_path)
    product_df = df[df['product'] == product_name].copy()
    product_df['date'] = pd.to_datetime(product_df['date'])
    product_df.set_index('date', inplace=True)
    daily_series = product_df['quantity'].resample('D').sum().fillna(0)
    return daily_series

def run_forecast(series, steps=7):
    model = ARIMA(series, order=(1, 1, 1))
    model_fit = model.fit()
    forecast = model_fit.forecast(steps=steps)
    return forecast

# Day 6 Logic: Plotting results
def plot_forecast(history, forecast, product_name):
    try:
        # 1. Setup the figure
        plt.figure(figsize=(10, 5))
        plt.plot(history.index, history.values, label='Historical Sales', marker='o')
        plt.plot(forecast.index, forecast.values, label='7-Day Forecast', linestyle='--', color='red', marker='x')
        
        plt.title(f"Demand Forecast for {product_name}")
        plt.xlabel("Date")
        plt.ylabel("Quantity")
        plt.legend()
        plt.grid(True)

        # 2. Show the plot (Interactive window)
        plt.show()

        # 3. Attempt to save, but don't crash if it fails
        # This keeps the "launch" safe even if file permissions are weird
        save_path = "forecast_results.png"
        plt.savefig(save_path)
        print(f"Success: Plot displayed and saved to {save_path}")

    except Exception as e:
        print(f"Visualization Note: The math worked, but the chart had an issue: {e}")

if __name__ == "__main__":
    data_file = os.path.join('data', 'sample_data.csv')
    ts_data = load_and_prep_data(data_file, 'Widget_A')
    predictions = run_forecast(ts_data)
    
    # Run the visualization
    plot_forecast(ts_data, predictions, 'Widget_A')