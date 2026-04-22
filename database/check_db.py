import sqlite3
import pandas as pd

# Connect to your database
conn = sqlite3.connect('database/portograph.db')

# Query the last 5 trades to see the status
query = "SELECT id, ticker, suggested_price, capital_allocated, status, date_suggested FROM trade_history ORDER BY id DESC LIMIT 5;"
df = pd.read_sql_query(query, conn)

print(df)
conn.close()