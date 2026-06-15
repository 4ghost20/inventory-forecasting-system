import sqlite3
import pandas as pd
import os
import hashlib
import secrets
from passlib.hash import pbkdf2_sha256

def init_db():
    """Initializes the database schema if tables do not exist."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    # User table with hashed password storage
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_sessions
                 (token_hash TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    
    # Inventory tracking
    c.execute('''CREATE TABLE IF NOT EXISTS inventory 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  current_stock INTEGER, reorder_point INTEGER)''')
    
    # Sales history for ARIMA forecasting
    c.execute('''CREATE TABLE IF NOT EXISTS sales 
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product TEXT, 
                  date TEXT, quantity INTEGER)''')

    try:
        c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_user_product
                     ON inventory(user_id, product)''')
    except sqlite3.IntegrityError:
        print("Warning: duplicate inventory products exist; unique index was not created.")
    conn.commit()
    conn.close()

def register_user(username, password):
    """Hashes password and saves new user if username is unique."""
    try:
        conn = sqlite3.connect('inventory_system.db')
        c = conn.cursor()
        
        # Hash the password before saving 
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

def create_user_session(user_id):
    """Creates a reload-safe login session token for the browser."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_sessions (token_hash, user_id, created_at) VALUES (?, ?, datetime('now'))",
        (token_hash, user_id)
    )
    conn.commit()
    conn.close()
    return token

def get_user_by_session(token):
    """Returns user details for a valid session token."""
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute(
        '''SELECT users.id, users.username
           FROM user_sessions
           JOIN users ON users.id = user_sessions.user_id
           WHERE user_sessions.token_hash = ?''',
        (token_hash,)
    )
    user = c.fetchone()
    conn.close()

    if user:
        return {'user_id': user[0], 'username': user[1]}
    return None

def delete_user_session(token):
    """Revokes a stored browser session token."""
    if not token:
        return

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    c.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()

def add_sales_record(user_id, product, date, quantity):
    """Transactional update: Logs sale and deducts inventory in one go."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    try:
        # Validate if date is not in the future
        sale_date = pd.to_datetime(date)
        today = pd.Timestamp.today()
        if sale_date > today:
            print("Error: Sale date cannot be in the future")
            conn.close()
            return False

        c.execute(
            '''UPDATE inventory
               SET current_stock = current_stock - ?
               WHERE product = ? AND user_id = ? AND current_stock >= ?''',
            (quantity, product, user_id, quantity)
        )

        if c.rowcount == 0:
            conn.rollback()
            print("Error: Not enough stock or product not found")
            return False

        c.execute("INSERT INTO sales (user_id, product, date, quantity) VALUES (?, ?, ?, ?)", 
                  (user_id, product, str(date), quantity))

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
    try:
        c.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND product = ?",
            (user_id, product_name)
        )
        if c.fetchone():
            return False

        c.execute('''INSERT INTO inventory (user_id, product, current_stock, reorder_point) 
                     VALUES (?, ?, ?, ?)''', (user_id, product_name, starting_stock, reorder_point))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def upsert_inventory_item(user_id, product_name, current_stock=None, reorder_point=None):
    """Creates or updates an inventory item for imported data."""
    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    try:
        c.execute(
            "SELECT id FROM inventory WHERE user_id = ? AND product = ?",
            (user_id, product_name)
        )
        row = c.fetchone()

        stock_value = 0 if current_stock is None else int(current_stock)
        reorder_value = 10 if reorder_point is None else int(reorder_point)

        if row:
            if current_stock is not None and reorder_point is not None:
                c.execute(
                    '''UPDATE inventory
                       SET current_stock = ?, reorder_point = ?
                       WHERE id = ? AND user_id = ?''',
                    (stock_value, reorder_value, row[0], user_id)
                )
            elif current_stock is not None:
                c.execute(
                    '''UPDATE inventory
                       SET current_stock = ?
                       WHERE id = ? AND user_id = ?''',
                    (stock_value, row[0], user_id)
                )
            elif reorder_point is not None:
                c.execute(
                    '''UPDATE inventory
                       SET reorder_point = ?
                       WHERE id = ? AND user_id = ?''',
                    (reorder_value, row[0], user_id)
                )
        else:
            c.execute(
                '''INSERT INTO inventory (user_id, product, current_stock, reorder_point)
                   VALUES (?, ?, ?, ?)''',
                (user_id, product_name, stock_value, reorder_value)
            )

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error upserting inventory item: {e}")
        return False
    finally:
        conn.close()

def bulk_import_sales(user_id, import_df):
    """Imports historical sales records and optional inventory columns for forecasting."""
    required_cols = {'date', 'product', 'quantity'}
    if not required_cols.issubset(import_df.columns):
        missing = sorted(required_cols - set(import_df.columns))
        return {'success': False, 'imported': 0, 'skipped': len(import_df), 'errors': [f"Missing columns: {', '.join(missing)}"]}

    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    imported = 0
    skipped = 0
    errors = []
    today = pd.Timestamp.today().normalize()

    try:
        for row_number, row in import_df.iterrows():
            line = row_number + 2
            try:
                product = str(row['product']).strip()
                sale_date = pd.to_datetime(row['date'], errors='coerce')
                quantity = pd.to_numeric(row['quantity'], errors='coerce')

                if not product or product.lower() == 'nan':
                    raise ValueError("product is empty")
                if pd.isna(sale_date):
                    raise ValueError("date is invalid")
                if sale_date.normalize() > today:
                    raise ValueError("date is in the future")
                if pd.isna(quantity) or int(quantity) <= 0:
                    raise ValueError("quantity must be greater than 0")

                current_stock = None
                reorder_point = None
                if 'current_stock' in import_df.columns and not pd.isna(row.get('current_stock')):
                    current_stock = int(pd.to_numeric(row.get('current_stock'), errors='raise'))
                    if current_stock < 0:
                        raise ValueError("current_stock cannot be negative")
                if 'reorder_point' in import_df.columns and not pd.isna(row.get('reorder_point')):
                    reorder_point = int(pd.to_numeric(row.get('reorder_point'), errors='raise'))
                    if reorder_point <= 0:
                        raise ValueError("reorder_point must be greater than 0")

                c.execute(
                    "SELECT id FROM inventory WHERE user_id = ? AND product = ?",
                    (user_id, product)
                )
                inventory_row = c.fetchone()

                stock_value = 0 if current_stock is None else current_stock
                reorder_value = 10 if reorder_point is None else reorder_point

                if inventory_row:
                    if current_stock is not None and reorder_point is not None:
                        c.execute(
                            '''UPDATE inventory
                               SET current_stock = ?, reorder_point = ?
                               WHERE id = ? AND user_id = ?''',
                            (stock_value, reorder_value, inventory_row[0], user_id)
                        )
                    elif current_stock is not None:
                        c.execute(
                            '''UPDATE inventory
                               SET current_stock = ?
                               WHERE id = ? AND user_id = ?''',
                            (stock_value, inventory_row[0], user_id)
                        )
                    elif reorder_point is not None:
                        c.execute(
                            '''UPDATE inventory
                               SET reorder_point = ?
                               WHERE id = ? AND user_id = ?''',
                            (reorder_value, inventory_row[0], user_id)
                        )
                else:
                    c.execute(
                        '''INSERT INTO inventory (user_id, product, current_stock, reorder_point)
                           VALUES (?, ?, ?, ?)''',
                        (user_id, product, stock_value, reorder_value)
                    )

                c.execute(
                    "INSERT INTO sales (user_id, product, date, quantity) VALUES (?, ?, ?, ?)",
                    (user_id, product, sale_date.strftime('%Y-%m-%d'), int(quantity))
                )
                imported += 1
            except Exception as e:
                skipped += 1
                if len(errors) < 5:
                    errors.append(f"Row {line}: {e}")

        conn.commit()
        return {'success': imported > 0, 'imported': imported, 'skipped': skipped, 'errors': errors}
    except Exception as e:
        conn.rollback()
        return {'success': False, 'imported': imported, 'skipped': skipped, 'errors': [str(e)]}
    finally:
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
    if table_name != 'sales':
        return False

    conn = sqlite3.connect('inventory_system.db')
    c = conn.cursor()
    try:
        c.execute(
            "SELECT product, quantity FROM sales WHERE id = ? AND user_id = ?",
            (record_id, user_id)
        )
        sale = c.fetchone()
        if not sale:
            return False

        product, quantity = sale
        c.execute("DELETE FROM sales WHERE id = ? AND user_id = ?", (record_id, user_id))
        c.execute(
            '''UPDATE inventory
               SET current_stock = current_stock + ?
               WHERE product = ? AND user_id = ?''',
            (quantity, product, user_id)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error deleting transaction: {e}")
        return False
    finally:
        conn.close()

def migrate_csv_to_sql(user_id):
    """One-time migration of legacy CSV data into the user's SQL account."""
    conn = sqlite3.connect('inventory_system.db')
    # Check if user already has data to prevent duplicates
    inv_count = pd.read_sql("SELECT COUNT(*) as count FROM inventory WHERE user_id = ?", conn, params=(user_id,))['count'][0]
    sales_count = pd.read_sql("SELECT COUNT(*) as count FROM sales WHERE user_id = ?", conn, params=(user_id,))['count'][0]
    
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
