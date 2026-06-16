import sqlite3
import pandas as pd
import os
import hashlib
import secrets
from passlib.hash import pbkdf2_sha256

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, 'inventory_system.db')
SESSION_DAYS = 7

os.makedirs(DATA_DIR, exist_ok=True)


def get_db_path():
    """Return the active database path, with test/deploy override support."""
    return os.environ.get('INVENTORY_DB_PATH', DEFAULT_DB_PATH)


def get_data_path(filename):
    """Return an absolute path inside the app data directory."""
    return os.path.join(DATA_DIR, filename)


def connect_db():
    """Open a SQLite connection to the active app database."""
    conn = sqlite3.connect(get_db_path(), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def column_exists(cursor, table_name, column_name):
    """Check whether a column exists before running simple migrations."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return column_name in [row[1] for row in cursor.fetchall()]


def ensure_auth_schema(cursor):
    """Add small auth/admin columns to older databases."""
    if not column_exists(cursor, 'users', 'is_admin'):
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if not column_exists(cursor, 'users', 'must_change_password'):
        cursor.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    if not column_exists(cursor, 'user_sessions', 'expires_at'):
        cursor.execute("ALTER TABLE user_sessions ADD COLUMN expires_at TEXT")
        cursor.execute(
            '''UPDATE user_sessions
               SET expires_at = datetime(created_at, ?)
               WHERE expires_at IS NULL''',
            (f'+{SESSION_DAYS} days',)
        )


def ensure_admin_exists(cursor):
    """Promote the first user if the database has users but no admin."""
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    if user_count == 0:
        return

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
    admin_count = cursor.fetchone()[0]
    if admin_count == 0:
        cursor.execute(
            "UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)"
        )


def cleanup_expired_sessions(days=SESSION_DAYS):
    """Delete old browser sessions so login tokens do not last forever."""
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        '''DELETE FROM user_sessions
           WHERE datetime(COALESCE(expires_at, created_at)) < datetime('now')
              OR datetime(created_at) < datetime('now', ?)''',
        (f'-{days} days',)
    )
    conn.commit()
    conn.close()

def init_db():
    """Initializes the database schema if tables do not exist."""
    conn = connect_db()
    c = conn.cursor()
    # User table with hashed password storage
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
                  is_admin INTEGER DEFAULT 0,
                  must_change_password INTEGER DEFAULT 0)''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_sessions
                 (token_hash TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT,
                  expires_at TEXT,
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
    ensure_auth_schema(c)
    ensure_admin_exists(c)
    conn.commit()
    conn.close()
    cleanup_expired_sessions()

def register_user(username, password):
    """Hashes password and saves new user if username is unique."""
    conn = None
    try:
        conn = connect_db()
        c = conn.cursor()
        
        # Hash the password before saving 
        hashed_password = pbkdf2_sha256.hash(password)
        
        c.execute("SELECT COUNT(*) FROM users")
        is_first_user = c.fetchone()[0] == 0

        c.execute(
            "INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
            (username, hashed_password, 1 if is_first_user else 0)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Username already exists
    finally:
        if conn:
            conn.close()

def verify_user(username, password):
    """Verifies credentials using secure hash comparison."""
    conn = connect_db()
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

    conn = connect_db()
    c = conn.cursor()
    c.execute(
        '''INSERT INTO user_sessions (token_hash, user_id, created_at, expires_at)
           VALUES (?, ?, datetime('now'), datetime('now', ?))''',
        (token_hash, user_id, f'+{SESSION_DAYS} days')
    )
    conn.commit()
    conn.close()
    return token

def get_user_by_session(token):
    """Returns user details for a valid session token."""
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        '''SELECT users.id, users.username, users.is_admin, users.must_change_password
           FROM user_sessions
           JOIN users ON users.id = user_sessions.user_id
           WHERE user_sessions.token_hash = ?
             AND datetime(COALESCE(user_sessions.expires_at, user_sessions.created_at)) >= datetime('now')
             AND datetime(user_sessions.created_at) >= datetime('now', ?)''',
        (token_hash, f'-{SESSION_DAYS} days')
    )
    user = c.fetchone()
    if not user:
        c.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()
    conn.close()

    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'is_admin': bool(user[2]),
            'must_change_password': bool(user[3])
        }
    return None

def delete_user_session(token):
    """Revokes a stored browser session token."""
    if not token:
        return

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = connect_db()
    c = conn.cursor()
    c.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()


def delete_user_sessions(user_id):
    """Revoke all browser sessions for one user."""
    conn = connect_db()
    c = conn.cursor()
    c.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_profile(user_id):
    """Return basic user flags for UI decisions."""
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, username, is_admin, must_change_password FROM users WHERE id = ?",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        'user_id': row[0],
        'username': row[1],
        'is_admin': bool(row[2]),
        'must_change_password': bool(row[3])
    }


def is_user_admin(user_id):
    """Return True when the user has admin rights."""
    profile = get_user_profile(user_id)
    return bool(profile and profile['is_admin'])


def list_users():
    """List users for the admin management page."""
    conn = connect_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''SELECT users.id, users.username, users.is_admin, users.must_change_password,
                  COUNT(user_sessions.token_hash) AS active_sessions
           FROM users
           LEFT JOIN user_sessions
             ON users.id = user_sessions.user_id
            AND datetime(COALESCE(user_sessions.expires_at, user_sessions.created_at)) >= datetime('now')
           GROUP BY users.id, users.username, users.is_admin, users.must_change_password
           ORDER BY users.username'''
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_user_admin(admin_user_id, target_user_id, is_admin):
    """Grant or remove admin rights. At least one admin must remain."""
    if not is_user_admin(admin_user_id):
        return False

    conn = connect_db()
    c = conn.cursor()
    if not is_admin:
        c.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND id != ?",
            (target_user_id,)
        )
        if c.fetchone()[0] == 0:
            conn.close()
            return False

    c.execute(
        "UPDATE users SET is_admin = ? WHERE id = ?",
        (1 if is_admin else 0, target_user_id)
    )
    conn.commit()
    updated = c.rowcount > 0
    conn.close()
    return updated


def reset_user_password(admin_user_id, target_user_id, new_password):
    """Admin password reset for a user account."""
    if not is_user_admin(admin_user_id) or len(new_password) < 6:
        return False

    hashed_password = pbkdf2_sha256.hash(new_password)
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        '''UPDATE users
           SET password = ?, must_change_password = 1
           WHERE id = ?''',
        (hashed_password, target_user_id)
    )
    updated = c.rowcount > 0
    if updated:
        c.execute("DELETE FROM user_sessions WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    return updated


def change_user_password(user_id, current_password, new_password):
    """Allow a logged-in user to change their own password."""
    if len(new_password) < 6:
        return False

    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    if not row or not pbkdf2_sha256.verify(current_password, row[0]):
        conn.close()
        return False

    hashed_password = pbkdf2_sha256.hash(new_password)
    c.execute(
        "UPDATE users SET password = ?, must_change_password = 0 WHERE id = ?",
        (hashed_password, user_id)
    )
    conn.commit()
    conn.close()
    return True


def clear_must_change_password(user_id):
    """Clear the forced password change flag after a successful change."""
    conn = connect_db()
    c = conn.cursor()
    c.execute("UPDATE users SET must_change_password = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_sales_record(user_id, product, date, quantity):
    """Transactional update: Logs sale and deducts inventory in one go."""
    conn = connect_db()
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
    conn = connect_db()
    c = conn.cursor()
    c.execute('''UPDATE inventory SET current_stock = current_stock + ? 
                 WHERE product = ? AND user_id = ?''', (added_qty, product_name, user_id))
    conn.commit()
    conn.close()

def add_new_inventory_item(user_id, product_name, starting_stock, reorder_point):
    """Registers a brand new product for the user."""
    conn = connect_db()
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
    conn = connect_db()
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

    conn = connect_db()
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

                normalized_date = sale_date.strftime('%Y-%m-%d')

                c.execute(
                    '''SELECT 1 FROM sales
                       WHERE user_id = ? AND product = ? AND date = ? AND quantity = ?
                       LIMIT 1''',
                    (user_id, product, normalized_date, int(quantity))
                )
                if c.fetchone():
                    skipped += 1
                    if len(errors) < 5:
                        errors.append(f"Row {line}: duplicate sale skipped")
                    continue

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
                    (user_id, product, normalized_date, int(quantity))
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
    conn = connect_db()
    c = conn.cursor()
    c.execute("DELETE FROM inventory WHERE product = ? AND user_id = ?", (product_name, user_id))
    c.execute("DELETE FROM sales WHERE product = ? AND user_id = ?", (product_name, user_id))
    conn.commit()
    conn.close()

def delete_transaction(table_name, record_id, user_id):
    """Removes a specific record (e.g., one mistaken sales entry)."""
    if table_name != 'sales':
        return False

    conn = connect_db()
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


def update_reorder_point(user_id, product_name, reorder_point):
    """Updates the reorder point for one product."""
    if reorder_point <= 0:
        return False

    conn = connect_db()
    c = conn.cursor()
    c.execute(
        '''UPDATE inventory
           SET reorder_point = ?
           WHERE product = ? AND user_id = ?''',
        (reorder_point, product_name, user_id)
    )
    conn.commit()
    updated = c.rowcount > 0
    conn.close()
    return updated

def migrate_csv_to_sql(user_id):
    """One-time migration of legacy CSV data into the user's SQL account."""
    conn = connect_db()
    # Check if user already has data to prevent duplicates
    inv_count = pd.read_sql("SELECT COUNT(*) as count FROM inventory WHERE user_id = ?", conn, params=(user_id,))['count'][0]
    sales_count = pd.read_sql("SELECT COUNT(*) as count FROM sales WHERE user_id = ?", conn, params=(user_id,))['count'][0]
    
    # Only migrate if no data exists for this user (prevents duplicates)
    if inv_count == 0 and sales_count == 0:
        # Load Inventory CSV
        inventory_path = get_data_path('current_inventory.csv')
        if os.path.exists(inventory_path):
            inv_df = pd.read_csv(inventory_path)
            inv_df['user_id'] = user_id
            # Filter to ensure we only push relevant columns
            cols = ['user_id', 'product', 'current_stock', 'reorder_point']
            inv_df[cols].to_sql('inventory', conn, if_exists='append', index=False)
            
        # Load Sales History CSV
        sample_path = get_data_path('sample_data.csv')
        if os.path.exists(sample_path):
            sales_df = pd.read_csv(sample_path)
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
