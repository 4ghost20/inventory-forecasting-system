import pandas as pd

# 1. Load dataset using pandas
df = pd.read_csv('data/sample_data.csv')

# 2. Display dataset
print("--- Original Dataset ---")
print(df)

# 3. Sort by date
# This ensures our time series is in the correct order for Week 1 Day 4
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values(by='date')

print("\n--- Sorted Dataset ---")
print(df)

# 4. Group by product
# This is a Day 3 requirement to see totals per item
grouped = df.groupby('product')['quantity'].sum()

print("\n--- Total Quantity per Product ---")
print(grouped)