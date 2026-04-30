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

# 1. Setup
st.set_page_config(page_title="Inventory AI", layout="wide", page_icon="📦")
init_db()

if 'logged_in' not in st.session_state:
    st.session_state.update({'logged_in': False, 'user_id': None, 'username': None, 'auth_mode': 'login'})

# --- AUTHENTICATION ---
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
                    uid = verify_user(u, p)
                    if uid:
                        st.session_state.update({'logged_in': True, 'user_id': uid, 'username': u})
                        st.rerun()
                    else: st.error("Invalid credentials.")
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
                    if np != cp: st.error("Passwords mismatch.")
                    elif register_user(nu, np):
                        st.success("Success! Please Login.")
                        st.session_state['auth_mode'] = 'login'
                    else: st.error("Username taken.")
            if st.button("Back to Login"):
                st.session_state['auth_mode'] = 'login'
                st.rerun()
    st.stop()

# --- APP CONTENT ---
else:
    uid = st.session_state['user_id']
    username = st.session_state['username']
    user_forecast_path = os.path.join('data', f'forecast_user_{uid}.csv')

    st.sidebar.title(f"👤 {username}")
    if st.sidebar.button("🚀 Run My Forecast"):
        with st.spinner("Analyzing history..."):
            if run_inventory_check(uid):
                st.sidebar.success("Forecast Updated!")
                st.rerun()
            else: st.sidebar.error("Need more sales data.")
    if st.sidebar.button("Logout"):
        st.session_state.update({'logged_in': False, 'user_id': None})
        st.rerun()

    page = st.sidebar.radio("Navigate", ["Dashboard", "Add Data", "Database View"])

    if page == "Dashboard":
        st.title(f"📊 {username}'s Dashboard")
        conn = sqlite3.connect('inventory_system.db')
        inv_df = pd.read_sql(f"SELECT * FROM inventory WHERE user_id = {uid}", conn)
        
        if not inv_df.empty:
            sel_prod = st.selectbox("Select Product", inv_df['product'].unique())
            prod_info = inv_df[inv_df['product'] == sel_prod].iloc[0]

            if os.path.exists(user_forecast_path):
                f_df = pd.read_csv(user_forecast_path)
                prod_f = f_df[f_df['product'] == sel_prod]
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Current Stock", f"{int(prod_info['current_stock'])}")
                if not prod_f.empty:
                    demand = int(prod_f['predicted_quantity'].sum())
                    c2.metric("7-Day Forecast", f"{demand}")
                    gap = int(prod_info['current_stock']) - demand
                    if gap < 0: c3.error(f"Shortage: {abs(gap)}")
                    else: c3.success(f"Surplus: {gap}")
                    st.line_chart(prod_f.set_index('forecast_date')['predicted_quantity'])
            else: st.warning("Run forecast to see results.")
        else: st.info("Inventory is empty.")
        conn.close()

    elif page == "Add Data":
        st.title("📥 Data Portal")
        col_s, col_r, col_n = st.columns(3)
        
        with col_s:
            st.subheader("🛒 Log Sale")
            with st.form("s_form"):
                ps = st.text_input("Product Name")
                ds = st.date_input("Date of Sale") # Dynamic date input
                qs = st.number_input("Qty", min_value=1)
                if st.form_submit_button("Submit Sale"):
                    conn = sqlite3.connect('inventory_system.db')
                    check = pd.read_sql(f"SELECT current_stock FROM inventory WHERE product='{ps}' AND user_id={uid}", conn)
                    conn.close()
                    if not check.empty and check.iloc[0]['current_stock'] >= qs:
                        add_sales_record(uid, ps, ds, qs) # Passing ds dynamically
                        st.success("Sale logged & stock deducted!")
                        st.rerun()
                    else: st.error("Insufficient stock or item not found.")

        with col_r:
            st.subheader("📦 Restock")
            conn = sqlite3.connect('inventory_system.db')
            prods = pd.read_sql(f"SELECT product FROM inventory WHERE user_id = {uid}", conn)['product'].tolist()
            conn.close()
            with st.form("r_form"):
                pr = st.selectbox("Product", prods if prods else ["None"])
                qr = st.number_input("Add Qty", min_value=1)
                if st.form_submit_button("Update"):
                    update_stock_level(uid, pr, qr)
                    st.success("Stock increased!")
                    st.rerun()

        with col_n:
            st.subheader("✨ New Item")
            with st.form("n_form"):
                pn = st.text_input("Name")
                sn = st.number_input("Opening Stock", min_value=0)
                if st.form_submit_button("Add"):
                    add_new_inventory_item(uid, pn, sn, 10)
                    st.success("Registered!")
                    st.rerun()

    elif page == "Database View":
        st.title("🗄️ Database Management")
        conn = sqlite3.connect('inventory_system.db')
        
        # DELETE TOOLS
        st.subheader("🛠️ Record Management")
        c_del1, c_del2 = st.columns(2)
        
        with c_del1:
            sales_hist = pd.read_sql(f"SELECT * FROM sales WHERE user_id = {uid} ORDER BY id DESC LIMIT 5", conn)
            if not sales_hist.empty:
                s_opts = [f"ID: {r['id']} | {r['product']} ({r['quantity']})" for _, r in sales_hist.iterrows()]
                to_del_s = st.selectbox("Select Sale ID to Delete", s_opts)
                if st.button("🗑️ Delete Single Sale"):
                    t_id = int(to_del_s.split("ID: ")[1].split(" |")[0])
                    delete_transaction('sales', t_id, uid)
                    st.rerun()

        with c_del2:
            inv_hist = pd.read_sql(f"SELECT * FROM inventory WHERE user_id = {uid}", conn)
            if not inv_hist.empty:
                i_opts = inv_hist['product'].tolist()
                to_del_i = st.selectbox("Select Product to Wipe", i_opts)
                if st.button("🚨 Wipe Product & History"):
                    delete_product_fully(uid, to_del_i)
                    st.success(f"{to_del_i} purged!")
                    st.rerun()

        st.markdown("---")
        st.write("Inventory Table")
        st.dataframe(pd.read_sql(f"SELECT * FROM inventory WHERE user_id = {uid}", conn), use_container_width=True)
        st.write("Sales History")
        st.dataframe(pd.read_sql(f"SELECT * FROM sales WHERE user_id = {uid}", conn), use_container_width=True)
        conn.close()