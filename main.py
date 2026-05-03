from models.forecaster import run_inventory_check
from models.analyzer import run_gap_analysis

def start_system(user_id=1):
    """Run full inventory analysis for a specific user."""
    print("🚀 BOOTING INVENTORY COMMAND CENTER...")
    
    # Step 1: Generate Forecasts 
    print("\n[STEP 1/2] Refreshing Forecast Data...")
    forecast_result = run_inventory_check(user_id)
    
    if not forecast_result:
        print("❌ Forecasting failed. Stopping.")
        return
    
    # Step 2: Run Analysis 
    print("\n[STEP 2/2] Analyzing Stock Gaps...")
    analysis_result = run_gap_analysis(user_id)
    
    if analysis_result:
        print("\n✅ SYSTEM RUN COMPLETE. CHECK ALERTS ABOVE.")
    else:
        print("\n⚠️ Analysis incomplete. Check forecast data.")

if __name__ == "__main__":
    start_system()