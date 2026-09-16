import os
import sqlite3
from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')
DB_NAME = 'r_pharmacy.db'

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            brand TEXT,
            agency TEXT,
            agency_code TEXT,
            description TEXT,
            master_case TEXT,
            master_case_no TEXT,
            dr_number TEXT,
            master_dr_no TEXT,
            start_date TEXT,
            end_date TEXT,
            budget REAL DEFAULT 0,
            budget_amount REAL DEFAULT 0,
            claimed REAL DEFAULT 0,
            claimed_amount REAL DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/backup-db')
def backup_db():
    return send_file(DB_NAME, as_attachment=True)

@app.route('/api/promotions', methods=['GET'])
def get_promotions():
    search = request.args.get('search', '').strip()
    brand = request.args.get('brand', '').strip()
    
    conn = get_db_connection()
    query = "SELECT * FROM promotions WHERE 1=1"
    params = []

    if brand:
        query += " AND brand = ?"
        params.append(brand)
    if search:
        query += " AND (description LIKE ? OR brand LIKE ? OR agency LIKE ? OR agency_code LIKE ? OR master_case LIKE ? OR master_case_no LIKE ? OR dr_number LIKE ? OR master_dr_no LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard] * 8)

    query += " ORDER BY id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify([dict(row) for row in rows])

@app.route('/api/promotions', methods=['POST'])
def add_promotion():
    data = request.json or {}
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO promotions (
            date, brand, agency, agency_code, description, 
            master_case, master_case_no, dr_number, master_dr_no, 
            start_date, end_date, budget, budget_amount, claimed, claimed_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('date', ''),
        data.get('brand', ''),
        data.get('agency') or data.get('agency_code', ''),
        data.get('agency_code') or data.get('agency', ''),
        data.get('description', ''),
        data.get('master_case') or data.get('master_case_no', ''),
        data.get('master_case_no') or data.get('master_case', ''),
        data.get('dr_number') or data.get('master_dr_no', ''),
        data.get('master_dr_no') or data.get('dr_number', ''),
        data.get('start_date', ''),
        data.get('end_date', ''),
        float(data.get('budget') or data.get('budget_amount') or 0),
        float(data.get('budget_amount') or data.get('budget') or 0),
        float(data.get('claimed') or data.get('claimed_amount') or 0),
        float(data.get('claimed_amount') or data.get('claimed') or 0)
    ))
    
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    
    return jsonify({"status": "success", "id": new_id}), 201

@app.route('/api/promotions/<int:item_id>', methods=['DELETE'])
def delete_promotion(item_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM promotions WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
