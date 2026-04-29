from models.forecaster import run_inventory_check
from models.analyzer import run_gap_analysis

def start_system():
    print("🚀 BOOTING INVENTORY COMMAND CENTER...")
    
    # Step 1: Generate Forecasts (Day 9 logic)
    print("\n[STEP 1/2] Refreshing Forecast Data...")
    run_inventory_check()
    
    # Step 2: Run Analysis (Day 10 logic)
    print("\n[STEP 2/2] Analyzing Stock Gaps...")
    run_gap_analysis()
    
    print("\n✅ SYSTEM RUN COMPLETE. CHECK ALERTS ABOVE.")

if __name__ == "__main__":
    start_system()