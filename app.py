import streamlit as st
import pandas as pd
import os
from models.database_manager import (
    init_db, register_user, verify_user, add_sales_record, 
    update_stock_level, add_new_inventory_item, delete_transaction, 
    delete_product_fully, create_user_session,
    get_user_by_session, delete_user_session, bulk_import_sales,
    get_data_path, update_reorder_point, connect_db, get_user_profile,
    change_user_password, list_users, reset_user_password, set_user_admin,
    delete_user_sessions
)
from models.forecaster import run_inventory_check
from models.analyzer import run_gap_analysis

def clear_user_forecast_cache(user_id):
    """Remove saved forecast outputs after data changes."""
    for filename in [f'forecast_user_{user_id}.csv', f'forecast_metrics_user_{user_id}.csv']:
        path = get_data_path(filename)
        if os.path.exists(path):
            os.remove(path)

def standardize_import_columns(df):
    """Normalize common spreadsheet column names to the app's expected names."""
    aliases = {
        'sale_date': 'date',
        'date_of_sale': 'date',
        'item': 'product',
        'item_name': 'product',
        'product_name': 'product',
        'qty': 'quantity',
        'qty_sold': 'quantity',
        'quantity_sold': 'quantity',
        'units_sold': 'quantity',
        'stock': 'current_stock',
        'opening_stock': 'current_stock',
        'available_stock': 'current_stock',
        'reorder': 'reorder_point',
        'minimum_stock': 'reorder_point'
    }
    normalized = df.copy()
    normalized.columns = [
        str(col).strip().lower().replace(' ', '_').replace('-', '_')
        for col in normalized.columns
    ]
    normalized.rename(columns=aliases, inplace=True)
    return normalized

# 1. Setup
st.set_page_config(page_title="Inventory AI", layout="wide", page_icon=":material/inventory_2:")
init_db()

if 'logged_in' not in st.session_state:
    st.session_state.update({
        'logged_in': False,
        'user_id': None,
        'username': None,
        'is_admin': False,
        'must_change_password': False,
        'auth_mode': 'login'
    })

if not st.session_state['logged_in']:
    session_token = st.query_params.get("session")
    saved_session = get_user_by_session(session_token)
    if saved_session:
        st.session_state.update({
            'logged_in': True,
            'user_id': saved_session['user_id'],
            'username': saved_session['username'],
            'is_admin': saved_session['is_admin'],
            'must_change_password': saved_session['must_change_password']
        })

# --- PHASE 1: AUTHENTICATION GATE ---
if not st.session_state['logged_in']:
    st.title("Inventory AI: Secure Portal")
    col_a, col_b, col_c = st.columns([1, 2, 1])
    
    with col_b:
        if st.session_state['auth_mode'] == 'login':
            st.subheader("Login")
            with st.form("login_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Sign In", icon=":material/login:"):
                    if not u or not p:
                        st.error("Please enter credentials.")
                    else:
                        uid = verify_user(u, p)
                        if uid:
                            profile = get_user_profile(uid)
                            st.session_state.update({
                                'logged_in': True,
                                'user_id': uid,
                                'username': u,
                                'is_admin': profile['is_admin'] if profile else False,
                                'must_change_password': profile['must_change_password'] if profile else False
                            })
                            st.query_params["session"] = create_user_session(uid)
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")
            
            if st.button("No account? Register here", icon=":material/person_add:"):
                st.session_state['auth_mode'] = 'register'
                st.rerun()

        else:
            st.subheader("Create Account")
            with st.form("reg_form"):
                nu = st.text_input("New Username")
                np = st.text_input("New Password", type="password")
                cp = st.text_input("Confirm Password", type="password")
                if st.form_submit_button("Register", icon=":material/person_add:"):
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
            
            if st.button("Back to Login", icon=":material/arrow_back:"):
                st.session_state['auth_mode'] = 'login'
                st.rerun()
    st.stop()

# --- PHASE 2: MAIN APPLICATION ---
else:
    uid = st.session_state['user_id']
    username = st.session_state['username']
    profile = get_user_profile(uid)
    if profile:
        st.session_state['is_admin'] = profile['is_admin']
        st.session_state['must_change_password'] = profile['must_change_password']
    user_forecast_path = get_data_path(f'forecast_user_{uid}.csv')

    if st.session_state.get('must_change_password'):
        st.title("Change Password")
        st.info("Your password was reset by an admin. Set a new password before continuing.")
        with st.form("forced_password_change"):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            if st.form_submit_button("Update Password", icon=":material/lock_reset:"):
                if new_password != confirm_password:
                    st.error("Passwords do not match.")
                elif len(new_password) < 6:
                    st.error("New password must be at least 6 characters.")
                elif change_user_password(uid, current_password, new_password):
                    st.session_state['must_change_password'] = False
                    st.success("Password updated.")
                    st.rerun()
                else:
                    st.error("Current password is incorrect.")

        if st.button("Logout", icon=":material/logout:"):
            delete_user_session(st.query_params.get("session"))
            st.query_params.clear()
            st.session_state.update({'logged_in': False, 'user_id': None, 'username': None})
            st.rerun()
        st.stop()

    # SIDEBAR
    st.sidebar.title(username)
    if st.sidebar.button("Run My Forecast", icon=":material/rocket_launch:"):
        with st.spinner("Analyzing history..."):
            if run_inventory_check(uid, force_refresh=True):
                st.sidebar.success("Forecast updated.")
                st.rerun()
            else:
                st.sidebar.error("Add sales history first. Seven daily records gives better forecasts.")
    
    if st.sidebar.button("Analyze Stock Gaps", icon=":material/query_stats:"):
        with st.spinner("Running gap analysis..."):
            if run_gap_analysis(uid):
                st.sidebar.success("Analysis complete.")
            else:
                st.sidebar.error("Run forecast first.")
    
    if st.sidebar.button("Logout", icon=":material/logout:"):
        delete_user_session(st.query_params.get("session"))
        st.query_params.clear()
        st.session_state.update({'logged_in': False, 'user_id': None})
        st.rerun()

    nav_pages = ["Dashboard", "Add Data", "Database View", "Account"]
    if st.session_state.get('is_admin'):
        nav_pages.append("Admin")
    page = st.sidebar.radio("Navigate", nav_pages)

    # --- DASHBOARD ---
    if page == "Dashboard":
        st.title(f"{username}'s Dashboard")
        conn = connect_db()
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
                    st.subheader("Detailed Forecast")
                    st.dataframe(prod_f, width="stretch")
                    
                    # Download button for forecast
                    csv = prod_f.to_csv(index=False)
                    st.download_button(
                        label="Download Forecast as CSV",
                        data=csv,
                        file_name=f"forecast_{sel_prod}_{pd.Timestamp.today().date()}.csv",
                        mime="text/csv",
                        icon=":material/download:"
                    )
                    
                    # Show forecast metrics if available
                    metrics_path = get_data_path(f'forecast_metrics_user_{uid}.csv')
                    if os.path.exists(metrics_path):
                        metrics_df = pd.read_csv(metrics_path)
                        prod_metrics = metrics_df[metrics_df['product'] == sel_prod]
                        if not prod_metrics.empty:
                            st.subheader("Forecast Quality")
                            metric_row = prod_metrics.iloc[0]
                            m1, m2, m3 = st.columns(3)
                            m1.metric("Data Points", int(metric_row['data_points']))
                            m2.metric("Forecast Status", metric_row['forecast_status'])
                            m3.metric("Accuracy", metric_row['accuracy'])

                            e1, e2, e3 = st.columns(3)
                            e1.metric("MAE", metric_row.get('mae', 'N/A'))
                            e2.metric("MSE", metric_row.get('mse', 'N/A'))
                            e3.metric("RMSE", metric_row.get('rmse', 'N/A'))

                            e4, e5 = st.columns(2)
                            e4.metric("MAPE", metric_row.get('mape', 'N/A'))
                            e5.metric("MASE", metric_row.get('mase', 'N/A'))

                            st.caption("MASE below 1.00 means the ARIMA forecast beat a naive forecast that simply repeats the last observed demand.")
                else:
                    c2.metric("7-Day Forecast", "No data")
                    c3.metric("Stock Gap", "N/A")
                    st.info("No forecast data available for this product. Run forecast again or add more sales history.")
            else:
                c2.metric("7-Day Forecast", "Not run")
                c3.metric("Stock Gap", "N/A")
                st.warning("Run forecast to see AI predictions.")
        else:
            st.info("Inventory is empty. Add items in 'Add Data'.")
        conn.close()

    # --- ADD DATA ---
    elif page == "Add Data":
        st.title("Data Portal")

        st.subheader("Bulk Import Sales Data")
        uploaded_file = st.file_uploader(
            "Upload CSV or Excel file",
            type=["csv", "xlsx", "xls"],
            help="Required columns: date, product, quantity. Optional columns: current_stock, reorder_point."
        )

        if uploaded_file is not None:
            try:
                if uploaded_file.name.lower().endswith(".csv"):
                    import_df = pd.read_csv(uploaded_file)
                else:
                    import_df = pd.read_excel(uploaded_file)

                import_df = standardize_import_columns(import_df)
                required = {'date', 'product', 'quantity'}
                missing = sorted(required - set(import_df.columns))

                if missing:
                    st.error(f"Missing required columns: {', '.join(missing)}")
                    st.caption("Use columns named date, product, quantity. Optional: current_stock, reorder_point.")
                else:
                    preview_cols = [col for col in ['date', 'product', 'quantity', 'current_stock', 'reorder_point'] if col in import_df.columns]
                    st.dataframe(import_df[preview_cols].head(20), width="stretch")
                    st.caption(f"Previewing first 20 rows from {len(import_df)} total rows.")

                    if st.button("Import File", icon=":material/upload_file:"):
                        result = bulk_import_sales(uid, import_df)
                        if result['success']:
                            clear_user_forecast_cache(uid)
                            st.success(f"Imported {result['imported']} sales rows. Skipped {result['skipped']} rows.")
                            if result['errors']:
                                st.warning("Some rows were skipped: " + " | ".join(result['errors']))
                        else:
                            st.error("Import failed. " + " | ".join(result['errors']))
            except ImportError:
                st.error("Excel support needs the openpyxl package. Run: pip install openpyxl")
            except Exception as e:
                st.error(f"Could not read file: {e}")

        st.markdown("---")
        col_s, col_r, col_n = st.columns(3)
        
        with col_s:
            st.subheader("Log Sale")
            with st.form("s_form"):
                ps = st.text_input("Product Name").strip()
                ds = st.date_input("Date of Sale")
                qs = st.number_input("Qty Sold", min_value=1)
                if st.form_submit_button("Submit Sale", icon=":material/point_of_sale:"):
                    if not ps or len(ps.strip()) == 0:
                        st.error("Product name cannot be empty.")
                    elif len(ps) > 100:
                        st.error("Product name too long (max 100 characters).")
                    elif qs <= 0:
                        st.error("Quantity must be greater than 0.")
                    elif ds > pd.Timestamp.today().date():
                        st.error("Sale date cannot be in the future.")
                    else:
                        conn = connect_db()
                        check = pd.read_sql("SELECT current_stock FROM inventory WHERE product=? AND user_id=?", conn, params=(ps, uid))
                        conn.close()
                        if not check.empty and check.iloc[0]['current_stock'] >= qs:
                            result = add_sales_record(uid, ps, ds, qs)
                            if result:
                                clear_user_forecast_cache(uid)
                                st.success("Sale logged and stock updated.")
                                st.rerun()
                            else:
                                st.error("Sale could not be logged. Check product stock and sale date.")
                        else:
                            st.error("Not enough stock or product not found.")

        with col_r:
            st.subheader("Restock")
            conn = connect_db()
            prods = pd.read_sql("SELECT product FROM inventory WHERE user_id = ?", conn, params=(uid,))['product'].tolist()
            conn.close()
            with st.form("r_form"):
                pr = st.selectbox("Product", prods if prods else ["None"])
                qr = st.number_input("Added Qty", min_value=1)
                if st.form_submit_button("Update Stock", icon=":material/inventory:"):
                    if pr == "None":
                        st.error("No products available.")
                    elif qr <= 0:
                        st.error("Quantity must be greater than 0.")
                    else:
                        update_stock_level(uid, pr, qr)
                        clear_user_forecast_cache(uid)
                        st.success(f"Added {qr} units to {pr}.")
                        st.rerun()

        with col_n:
            st.subheader("New Item")
            with st.form("n_form"):
                pn = st.text_input("Product Name").strip()
                sn = st.number_input("Opening Stock", min_value=0)
                rp = st.number_input("Reorder Point", min_value=1, value=10, 
                                   help="Stock level that triggers reordering")
                if st.form_submit_button("Register Product", icon=":material/add_box:"):
                    if not pn or len(pn.strip()) == 0:
                        st.error("Product name cannot be empty.")
                    elif len(pn) > 100:
                        st.error("Product name too long (max 100 characters).")
                    elif sn < 0:
                        st.error("Stock cannot be negative.")
                    elif rp <= 0:
                        st.error("Reorder point must be greater than 0.")
                    else:
                        if add_new_inventory_item(uid, pn, sn, rp):
                            clear_user_forecast_cache(uid)
                            st.success(f"{pn} registered with reorder point at {rp} units.")
                            st.rerun()
                        else:
                            st.error("Product already exists. Use Restock to add more units.")

    # --- DATABASE VIEW ---
    elif page == "Database View":
        st.title("Records")
        conn = connect_db()
        
        st.subheader("Management")
        c_del1, c_del2 = st.columns(2)
        
        with c_del1:
            sales_hist = pd.read_sql("SELECT * FROM sales WHERE user_id = ? ORDER BY id DESC LIMIT 20", conn, params=(uid,))
            if not sales_hist.empty:
                s_opts = [f"ID: {r['id']} | {r['product']} ({r['quantity']})" for _, r in sales_hist.iterrows()]
                to_del_s = st.selectbox("Select Sale to Delete", s_opts)
                if st.button("Delete Sale Entry", icon=":material/delete:"):
                    t_id = int(to_del_s.split("ID: ")[1].split(" |")[0])
                    if delete_transaction('sales', t_id, uid):
                        clear_user_forecast_cache(uid)
                    st.rerun()

        with c_del2:
            inv_hist = pd.read_sql("SELECT * FROM inventory WHERE user_id = ?", conn, params=(uid,))
            if not inv_hist.empty:
                i_opts = inv_hist['product'].tolist()
                to_del_i = st.selectbox("Select Product to Wipe", i_opts)
                if st.button("Purge Product & History", icon=":material/delete_forever:"):
                    delete_product_fully(uid, to_del_i)
                    clear_user_forecast_cache(uid)
                    st.rerun()

        st.subheader("Reorder Point")
        inv_edit = pd.read_sql("SELECT product, reorder_point FROM inventory WHERE user_id = ? ORDER BY product", conn, params=(uid,))
        if not inv_edit.empty:
            with st.form("reorder_form"):
                edit_product = st.selectbox("Product to Update", inv_edit['product'].tolist())
                current_reorder = int(inv_edit[inv_edit['product'] == edit_product]['reorder_point'].iloc[0])
                new_reorder = st.number_input("New Reorder Point", min_value=1, value=current_reorder)
                if st.form_submit_button("Save Reorder Point", icon=":material/save:"):
                    if update_reorder_point(uid, edit_product, int(new_reorder)):
                        clear_user_forecast_cache(uid)
                        st.success("Reorder point updated.")
                        st.rerun()
                    else:
                        st.error("Could not update reorder point.")

        st.markdown("---")
        st.subheader("Inventory Status")
        st.dataframe(pd.read_sql("SELECT product, current_stock, reorder_point FROM inventory WHERE user_id = ?", conn, params=(uid,)), width="stretch")
        st.subheader("Full Sales History")
        st.dataframe(pd.read_sql("SELECT id, product, date, quantity FROM sales WHERE user_id = ? ORDER BY id DESC", conn, params=(uid,)), width="stretch")
        conn.close()

    elif page == "Account":
        st.title("Account")
        st.subheader("Change Password")
        with st.form("change_password_form"):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            if st.form_submit_button("Update Password", icon=":material/lock_reset:"):
                if new_password != confirm_password:
                    st.error("Passwords do not match.")
                elif len(new_password) < 6:
                    st.error("New password must be at least 6 characters.")
                elif change_user_password(uid, current_password, new_password):
                    st.success("Password updated.")
                else:
                    st.error("Current password is incorrect.")

        st.subheader("Session")
        st.caption("Login sessions expire automatically after 7 days.")
        if st.button("Logout Everywhere", icon=":material/logout:"):
            delete_user_sessions(uid)
            st.query_params.clear()
            st.session_state.update({'logged_in': False, 'user_id': None, 'username': None})
            st.rerun()

    elif page == "Admin":
        if not st.session_state.get('is_admin'):
            st.error("Admin access required.")
            st.stop()

        st.title("Admin Management")
        users_df = pd.DataFrame(list_users())
        if users_df.empty:
            st.info("No users found.")
        else:
            display_df = users_df.rename(columns={
                'id': 'ID',
                'username': 'Username',
                'is_admin': 'Admin',
                'must_change_password': 'Must Change Password',
                'active_sessions': 'Active Sessions'
            })
            st.dataframe(display_df, width="stretch")

            options = [
                f"{row['id']} | {row['username']}"
                for _, row in users_df.iterrows()
            ]
            selected = st.selectbox("Select User", options)
            target_user_id = int(selected.split(" | ")[0])
            target_row = users_df[users_df['id'] == target_user_id].iloc[0]

            c1, c2 = st.columns(2)
            with c1:
                with st.form("admin_password_reset"):
                    st.subheader("Reset Password")
                    temp_password = st.text_input("Temporary Password", type="password")
                    confirm_temp_password = st.text_input("Confirm Temporary Password", type="password")
                    if st.form_submit_button("Reset Password", icon=":material/lock_reset:"):
                        if temp_password != confirm_temp_password:
                            st.error("Passwords do not match.")
                        elif len(temp_password) < 6:
                            st.error("Temporary password must be at least 6 characters.")
                        elif reset_user_password(uid, target_user_id, temp_password):
                            st.success("Password reset. The user must change it at next login.")
                        else:
                            st.error("Could not reset password.")

            with c2:
                st.subheader("Permissions")
                make_admin = st.checkbox("Admin User", value=bool(target_row['is_admin']))
                if st.button("Save Permissions", icon=":material/admin_panel_settings:"):
                    if set_user_admin(uid, target_user_id, make_admin):
                        st.success("Permissions updated.")
                        st.rerun()
                    else:
                        st.error("Could not update permissions. At least one admin must remain.")

                if st.button("Revoke User Sessions", icon=":material/no_accounts:"):
                    delete_user_sessions(target_user_id)
                    st.success("User sessions revoked.")
