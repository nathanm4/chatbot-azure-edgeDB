# test.py
import pyodbc
from langchain_community.utilities import SQLDatabase


# server   = "db-employez-azure-sql-server-dev-eastus.database.windows.net"
# database = "demoez"
# user     = "demo"
# password = "D3m0#3mploy3z.ai!"   # replace with actual password
# driver   = "ODBC Driver 18 for SQL Server"

# conn_str = (
#     f"DRIVER={{{driver}}};"
#     f"SERVER={server};"
#     f"DATABASE={database};"
#     f"UID={user};"
#     f"PWD={password};"
#     "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
# )

# try:
#     conn = pyodbc.connect(conn_str)
#     print("✅ Raw pyodbc connection OK")
#     conn.close()
# except Exception as e:
#     print("❌ pyodbc.connect failed:", e)
    
    


db = SQLDatabase.from_uri("mssql+pyodbc://sa:password123@localhost:1433/demoez?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=no&TrustServerCertificate=yes")

print("✅ Connected!")
print("Tables found:", db.get_usable_table_names())
