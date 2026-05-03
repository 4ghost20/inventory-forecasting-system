import sqlite3
import pandas as pd
import os
from passlib.hash import pbkdf2_sha256

def init_db():
    """Initializes the database schema if tables do not exist."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    # User table with hashed password storage
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)''')
    
    # Inventory tracking
    c.execute('''CREATE TABLE IF NOT EXISTS inventory 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  current_stock INTEGER, reorder_point INTEGER)''')
    
    # Sales history for ARIMA forecasting
    c.execute('''CREATE TABLE IF NOT EXISTS sales 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  date TEXT, quantity INTEGER)''')
    conn.commit()
    conn.close()

def register_user(username, password):
    """Hashes password and saves new user if username is unique."""
    try:
        conn = sqlite3.connect('inventory_system.db')
        c = conn.cursor()
        
        # Hash the password before saving (Security Best Practice)
        hashed_password = pbkdf2_sha256.hash(password)
        
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Username already exists
    finally:
        conn.close()

def verify_user(username, password):
    """Verifies credentials using secure hash comparison."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()
    
    if user and pbkdf2_sha256.verify(password, user[1]):
        return user[0]  # Returns the user_id
    return None

def add_sales_record(user_id, product, date, quantity):
    """Transactional update: Logs sale and deducts inventory in one go."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    try:
        # Validate that date is not in the future
        sale_date = pd.to_datetime(date)
        today = pd.Timestamp.today()
        if sale_date > today:
            print("Error: Sale date cannot be in the future")
            conn.close()
            return False
            
        # 1. Record the sale
        c.execute("INSERT INTO sales (user_id, product, date, quantity) VALUES (?, ?, ?, ?)", 
                  (user_id, product, str(date), quantity))
        
        # 2. Update the stock level
        c.execute('''UPDATE inventory SET current_stock = current_stock - ? 
                     WHERE product = ? AND user_id = ?''', (quantity, product, user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error logging sale: {e}")
        return False
    finally:
        conn.close()

def update_stock_level(user_id, product_name, added_qty):
    """Adds stock to an existing product."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute('''UPDATE inventory SET current_stock = current_stock + ? 
                 WHERE product = ? AND user_id = ?''', (added_qty, product_name, user_id))
    conn.commit()
    conn.close()

def add_new_inventory_item(user_id, product_name, starting_stock, reorder_point):
    """Registers a brand new product for the user."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute('''INSERT INTO inventory (user_id, product, current_stock, reorder_point) 
                 VALUES (?, ?, ?, ?)''', (user_id, product_name, starting_stock, reorder_point))
    conn.commit()
    conn.close()

def delete_product_fully(user_id, product_name):
    """Removes a product and all associated sales history to clean up forecast data."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE product = ? AND user_id = ?", (product_name, user_id))
    c.execute("DELETE FROM sales WHERE product = ? AND user_id = ?", (product_name, user_id))
    conn.commit()
    conn.close()

def delete_transaction(table_name, record_id, user_id):
    """Removes a specific record (e.g., one mistaken sales entry)."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    # Secure query formatting
    query = f"DELETE FROM {table_name} WHERE id = ? AND user_id = ?"
    c.execute(query, (record_id, user_id))
    conn.commit()
    conn.close()

def migrate_csv_to_sql(user_id):
    """One-time migration of legacy CSV data into the user's SQL account."""
    conn = sqlite3.connect('inventory_system.db')
    # Check if user already has data to prevent duplicates
    inv_count = pd.read_sql(f"SELECT COUNT(*) as count FROM inventory WHERE user_id = {user_id}", conn)['count'][0]
    sales_count = pd.read_sql(f"SELECT COUNT(*) as count FROM sales WHERE user_id = {user_id}", conn)['count'][0]
    
    # Only migrate if no data exists for this user (prevents duplicates)
    if inv_count == 0 and sales_count == 0:
        # Load Inventory CSV
        if os.path.exists('data/current_inventory.csv'):
            inv_df = pd.read_csv('data/current_inventory.csv')
            inv_df['user_id'] = user_id
            # Filter to ensure we only push relevant columns
            cols = ['user_id', 'product', 'current_stock', 'reorder_point']
            inv_df[cols].to_sql('inventory', conn, if_exists='append', index=False)
            
        # Load Sales History CSV
        if os.path.exists('data/sample_data.csv'):
            sales_df = pd.read_csv('data/sample_data.csv')
            # Validate dates are not in future
            sales_df['date'] = pd.to_datetime(sales_df['date'])
            today = pd.Timestamp.today()
            valid_sales = sales_df[sales_df['date'] <= today]  # Only keep past dates
            
            if not valid_sales.empty:
                valid_sales['user_id'] = user_id
                valid_sales['date'] = valid_sales['date'].dt.strftime('%Y-%m-%d')
                # Ensure columns match the SQL schema
                valid_sales = valid_sales.rename(columns={'Product': 'product', 'Quantity': 'quantity', 'Date': 'date'})
                cols = ['user_id', 'product', 'date', 'quantity']
                valid_sales[cols].to_sql('sales', conn, if_exists='append', index=False)
            
    conn.close()