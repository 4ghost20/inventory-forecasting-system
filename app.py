import streamlit as st
import pandas as pd
import sqlite3
import os
from models.database_manager import (
    init_db, register_user, verify_user, add_sales_record, 
    update_stock_level, add_new_inventory_item, delete_transaction, 
    migrate_csv_to_sql, delete_product_fully
)
from models.forecaster import run_inventory_check
from models.analyzer import run_gap_analysis

# 1. Setup
st.set_page_config(page_title="Inventory AI", layout="wide", page_icon="📦")
init_db()

if 'logged_in' not in st.session_state:
    st.session_state.update({'logged_in': False, 'user_id': None, 'username': None, 'auth_mode': 'login'})

# --- PHASE 1: AUTHENTICATION GATE ---
if not st.session_state['logged_in']:
    st.title("🔐 Inventory AI: Secure Portal")
    col_a, col_b, col_c = st.columns([1, 2, 1])
    
    with col_b:
        if st.session_state['auth_mode'] == 'login':
            st.subheader("Login")
            with st.form("login_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Sign In"):
                    if not u or not p:
                        st.error("Please enter credentials.")
                    else:
                        uid = verify_user(u, p)
                        if uid:
                            st.session_state.update({'logged_in': True, 'user_id': uid, 'username': u})
                            migrate_csv_to_sql(uid)
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")
            
            if st.button("No account? Register here"):
                st.session_state['auth_mode'] = 'register'
                st.rerun()

        else:
            st.subheader("Create Account")
            with st.form("reg_form"):
                nu = st.text_input("New Username")
                np = st.text_input("New Password", type="password")
                cp = st.text_input("Confirm Password", type="password")
                if st.form_submit_button("Register"):
                    if np != cp:
                        st.error("Passwords do not match.")
                    elif len(nu) < 3 or len(np) < 6:
                        st.error("Username (3+) and Password (6+) are too short.")
                    elif register_user(nu, np):
                        st.success("Account created! Switching to login...")
                        st.session_state['auth_mode'] = 'login'
                        st.rerun()
                    else:
                        st.error("Username already taken.")
            
            if st.button("Back to Login"):
                st.session_state['auth_mode'] = 'login'
                st.rerun()
    st.stop()

# --- PHASE 2: MAIN APPLICATION ---
else:
    uid = st.session_state['user_id']
    username = st.session_state['username']
    user_forecast_path = os.path.join('data', f'forecast_user_{uid}.csv')

    # SIDEBAR
    st.sidebar.title(f"👤 {username}")
    if st.sidebar.button("🚀 Run My Forecast"):
        with st.spinner("Analyzing history..."):
            if run_inventory_check(uid):
                st.sidebar.success("✅ Forecast Updated!")
                st.rerun()
            else:
                st.sidebar.error("❌ Not enough sales data. Need at least 3 days of history.")
    
    if st.sidebar.button("📊 Analyze Stock Gaps"):
        with st.spinner("Running gap analysis..."):
            if run_gap_analysis(uid):
                st.sidebar.success("✅ Analysis Complete!")
            else:
                st.sidebar.error("❌ Run forecast first.")
    
    if st.sidebar.button("Logout"):
        st.session_state.update({'logged_in': False, 'user_id': None})
        st.rerun()

    page = st.sidebar.radio("Navigate", ["Dashboard", "Add Data", "Database View"])

    # --- DASHBOARD ---
    if page == "Dashboard":
        st.title(f"📊 {username}'s Dashboard")
        conn = sqlite3.connect('inventory_system.db')
        inv_df = pd.read_sql("SELECT * FROM inventory WHERE user_id = ?", conn, params=(uid,))
        
        if not inv_df.empty:
            sel_prod = st.selectbox("Select Product", inv_df['product'].unique())
            prod_info = inv_df[inv_df['product'] == sel_prod].iloc[0]

            c1, c2, c3 = st.columns(3)
            c1.metric("Current Stock", f"{int(prod_info['current_stock'])}")
            
            if os.path.exists(user_forecast_path):
                f_df = pd.read_csv(user_forecast_path)
                prod_f = f_df[f_df['product'] == sel_prod]
                
                if not prod_f.empty:
                    demand = int(prod_f['predicted_quantity'].sum())
                    c2.metric("7-Day Forecast", f"{demand}")
                    gap = int(prod_info['current_stock']) - demand
                    if gap < 0:
                        c3.error(f"Shortage: {abs(gap)}")
                    else:
                        c3.success(f"Surplus: {gap}")
                    
                    st.line_chart(prod_f.set_index('forecast_date')['predicted_quantity'])
                    st.subheader("📈 Detailed Forecast")
                    st.dataframe(prod_f, use_container_width=True)
                    
                    # Download button for forecast
                    csv = prod_f.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Forecast as CSV",
                        data=csv,
                        file_name=f"forecast_{sel_prod}_{pd.Timestamp.today().date()}.csv",
                        mime="text/csv"
                    )
                    
                    # Show forecast metrics if available
                    metrics_path = os.path.join('data', f'forecast_metrics_user_{uid}.csv')
                    if os.path.exists(metrics_path):
                        metrics_df = pd.read_csv(metrics_path)
                        prod_metrics = metrics_df[metrics_df['product'] == sel_prod]
                        if not prod_metrics.empty:
                            st.subheader("📊 Forecast Quality")
                            m1, m2, m3 = st.columns(3)
                            m1.metric("Data Points", int(prod_metrics['data_points'].values[0]))
                            m2.metric("Forecast Status", prod_metrics['forecast_status'].values[0])
                            m3.metric("Accuracy", prod_metrics['accuracy'].values[0])
                else:
                    c2.metric("7-Day Forecast", "No data")
                    c3.metric("Stock Gap", "N/A")
                    st.info("No forecast data available for this product. Run forecast again or add more sales history.")
            else:
                c2.metric("7-Day Forecast", "Not run")
                c3.metric("Stock Gap", "N/A")
                st.warning("🔄 Run forecast to see AI predictions.")
        else:
            st.info("Inventory is empty. Add items in 'Add Data'.")
        conn.close()

    # --- ADD DATA ---
    elif page == "Add Data":
        st.title("📥 Data Portal")
        col_s, col_r, col_n = st.columns(3)
        
        with col_s:
            st.subheader("🛒 Log Sale")
            with st.form("s_form"):
                ps = st.text_input("Product Name").strip()
                ds = st.date_input("Date of Sale")
                qs = st.number_input("Qty Sold", min_value=1)
                if st.form_submit_button("Submit Sale"):
                    if not ps or len(ps.strip()) == 0:
                        st.error("❌ Product name cannot be empty.")
                    elif len(ps) > 100:
                        st.error("❌ Product name too long (max 100 characters).")
                    elif qs <= 0:
                        st.error("❌ Quantity must be greater than 0.")
                    elif ds > pd.Timestamp.today():
                        st.error("❌ Sale date cannot be in the future.")
                    else:
                        conn = sqlite3.connect('inventory_system.db')
                        check = pd.read_sql("SELECT current_stock FROM inventory WHERE product=? AND user_id=?", conn, params=(ps, uid))
                        conn.close()
                        if not check.empty and check.iloc[0]['current_stock'] >= qs:
                            result = add_sales_record(uid, ps, ds, qs)
                            if result:
                                st.success("✅ Sale logged & stock updated!")
                                st.rerun()
                            else:
                                st.error("❌ Sale date cannot be in the future.")
                        else:
                            st.error("❌ Not enough stock or product not found.")

        with col_r:
            st.subheader("📦 Restock")
            conn = sqlite3.connect('inventory_system.db')
            prods = pd.read_sql(f"SELECT product FROM inventory WHERE user_id = {uid}", conn)['product'].tolist()
            conn.close()
            with st.form("r_form"):
                pr = st.selectbox("Product", prods if prods else ["None"])
                qr = st.number_input("Added Qty", min_value=1)
                if st.form_submit_button("Update Stock"):
                    if pr == "None":
                        st.error("❌ No products available.")
                    elif qr <= 0:
                        st.error("❌ Quantity must be greater than 0.")
                    else:
                        update_stock_level(uid, pr, qr)
                        st.success(f"✅ Added {qr} units to {pr}.")
                        st.rerun()

        with col_n:
            st.subheader("✨ New Item")
            with st.form("n_form"):
                pn = st.text_input("Product Name").strip()
                sn = st.number_input("Opening Stock", min_value=0)
                rp = st.number_input("Reorder Point", min_value=1, value=10, 
                                   help="Stock level that triggers reordering")
                if st.form_submit_button("Register Product"):
                    if not pn or len(pn.strip()) == 0:
                        st.error("❌ Product name cannot be empty.")
                    elif len(pn) > 100:
                        st.error("❌ Product name too long (max 100 characters).")
                    elif sn < 0:
                        st.error("❌ Stock cannot be negative.")
                    elif rp <= 0:
                        st.error("❌ Reorder point must be greater than 0.")
                    else:
                        add_new_inventory_item(uid, pn, sn, rp)
                        st.success(f"✅ {pn} registered with reorder point at {rp} units.")
                        st.rerun()

    # --- DATABASE VIEW ---
    elif page == "Database View":
        st.title("🗄️ Records")
        conn = sqlite3.connect('inventory_system.db')
        
        st.subheader("🛠️ Management")
        c_del1, c_del2 = st.columns(2)
        
        with c_del1:
            sales_hist = pd.read_sql("SELECT * FROM sales WHERE user_id = ? ORDER BY id DESC LIMIT 5", conn, params=(uid,))
            if not sales_hist.empty:
                s_opts = [f"ID: {r['id']} | {r['product']} ({r['quantity']})" for _, r in sales_hist.iterrows()]
                to_del_s = st.selectbox("Select Sale to Delete", s_opts)
                if st.button("Delete Sale Entry"):
                    t_id = int(to_del_s.split("ID: ")[1].split(" |")[0])
                    delete_transaction('sales', t_id, uid)
                    st.rerun()

        with c_del2:
            inv_hist = pd.read_sql("SELECT * FROM inventory WHERE user_id = ?", conn, params=(uid,))
            if not inv_hist.empty:
                i_opts = inv_hist['product'].tolist()
                to_del_i = st.selectbox("Select Product to Wipe", i_opts)
                if st.button("🚨 Purge Product & History"):
                    delete_product_fully(uid, to_del_i)
                    st.rerun()

        st.markdown("---")
        st.subheader("Inventory Status")
        st.dataframe(pd.read_sql("SELECT product, current_stock FROM inventory WHERE user_id = ?", conn, params=(uid,)), use_container_width=True)
        st.subheader("Full Sales History")
        st.dataframe(pd.read_sql("SELECT id, product, date, quantity FROM sales WHERE user_id = ? ORDER BY id DESC", conn, params=(uid,)), use_container_width=True)
        conn.close()