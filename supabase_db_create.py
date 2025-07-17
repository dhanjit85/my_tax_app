import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv('DB_URL')

CREATE_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS UserFinancials (
    session_id UUID PRIMARY KEY,
    gross_salary NUMERIC(15, 2),
    basic_salary NUMERIC(15, 2),
    hra_received NUMERIC(15, 2),
    rent_paid NUMERIC(15, 2),
    deduction_80c NUMERIC(15, 2),
    deduction_80d NUMERIC(15, 2),
    standard_deduction NUMERIC(15, 2),
    professional_tax NUMERIC(15, 2),
    tds NUMERIC(15, 2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
'''

CREATE_TAX_COMPARISON_SQL = '''
CREATE TABLE IF NOT EXISTS TaxComparison (
    id SERIAL PRIMARY KEY,
    session_id UUID REFERENCES UserFinancials(session_id),
    tax_old_regime NUMERIC(15, 2),
    tax_new_regime NUMERIC(15, 2),
    best_regime VARCHAR(10),
    selected_regime VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
'''

def main():
    if not DB_URL:
        print("DB_URL not set in environment.")
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_TAX_COMPARISON_SQL)
        conn.commit()
        cur.close()
        conn.close()
        print("UserFinancials and TaxComparison tables created or already exist.")
    except Exception as e:
        print(f"Error creating table: {e}")

if __name__ == "__main__":
    main() 