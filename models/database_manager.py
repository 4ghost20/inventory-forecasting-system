import sqlite3
import pandas as pd
import os

def init_db():
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  current_stock INTEGER, reorder_point INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sales 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  date TEXT, quantity INTEGER)''')
    conn.commit()
    conn.close()

def register_user(username, password):
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        return False
    c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
    conn.commit()
    conn.close()
    return True

def verify_user(username, password):
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ? AND password = ?", (username, password))
    user = c.fetchone()
    conn.close()
    return user[0] if user else None

def add_sales_record(user_id, product, date, quantity):
    """Logs a sale and automatically deducts quantity from inventory."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    # 1. Record the sale with the dynamic date provided by the user
    c.execute("INSERT INTO sales (user_id, product, date, quantity) VALUES (?, ?, ?, ?)", 
              (user_id, product, str(date), quantity))
    # 2. Update the stock level (Functional Link)
    c.execute('''UPDATE inventory SET current_stock = current_stock - ? 
                 WHERE product = ? AND user_id = ?''', (quantity, product, user_id))
    conn.commit()
    conn.close()

def update_stock_level(user_id, product_name, added_qty):
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute('''UPDATE inventory SET current_stock = current_stock + ? 
                 WHERE product = ? AND user_id = ?''', (added_qty, product_name, user_id))
    conn.commit()
    conn.close()

def add_new_inventory_item(user_id, product_name, starting_stock, reorder_point):
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute('''INSERT INTO inventory (user_id, product, current_stock, reorder_point) 
                 VALUES (?, ?, ?, ?)''', (user_id, product_name, starting_stock, reorder_point))
    conn.commit()
    conn.close()

def delete_product_fully(user_id, product_name):
    """Removes a product and all associated sales history for a user."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE product = ? AND user_id = ?", (product_name, user_id))
    c.execute("DELETE FROM sales WHERE product = ? AND user_id = ?", (product_name, user_id))
    conn.commit()
    conn.close()

def delete_transaction(table_name, record_id, user_id):
    """Deletes a specific record by ID (e.g., a single sales entry)."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    query = f"DELETE FROM {table_name} WHERE id = ? AND user_id = ?"
    c.execute(query, (record_id, user_id))
    conn.commit()
    conn.close()

def migrate_csv_to_sql(user_id):
    conn = sqlite3.connect('inventory_system.db')
    inv_count = pd.read_sql(f"SELECT COUNT(*) as count FROM inventory WHERE user_id = {user_id}", conn)['count'][0]
    if inv_count == 0:
        if os.path.exists('data/current_inventory.csv'):
            inv_df = pd.read_csv('data/current_inventory.csv')
            inv_df['user_id'] = user_id
            inv_df.to_sql('inventory', conn, if_exists='append', index=False)
        if os.path.exists('data/sample_data.csv'):
            sales_df = pd.read_csv('data/sample_data.csv')
            sales_df['user_id'] = user_id
            sales_df.to_sql('sales', conn, if_exists='append', index=False)
    conn.close()