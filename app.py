from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

# Google Calendar API Imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

app = Flask(__name__, static_folder='static', static_url_path='')

DB_NAME = 'r_pharmacy.db'
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS master_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL,
            date TEXT,
            agency TEXT,
            description TEXT NOT NULL,
            master_case_no TEXT,
            master_dr_no TEXT,
            amount_budget REAL DEFAULT 0.0,
            status TEXT DEFAULT 'Active'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promotion_splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_id INTEGER,
            promo_start TEXT,
            promo_end TEXT,
            is_split INTEGER DEFAULT 0,
            split_case_no TEXT,
            split_dr_no TEXT,
            split_amount REAL DEFAULT 0.0,
            split_description TEXT,
            ap_claim_case TEXT,
            customer_invoice TEXT,
            amount REAL DEFAULT 0.0,
            FOREIGN KEY (master_id) REFERENCES master_promotions (id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute("PRAGMA table_info(promotion_splits)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'split_amount' not in columns:
        cursor.execute("ALTER TABLE promotion_splits ADD COLUMN split_amount REAL DEFAULT 0.0")
    if 'split_description' not in columns:
        cursor.execute("ALTER TABLE promotion_splits ADD COLUMN split_description TEXT")
        
    conn.commit()
    conn.close()

init_db()

# =========================================================================
# Google Calendar API 服务与每日定时任务逻辑
# =========================================================================

def get_calendar_service():
    """获取 Google Calendar 服务（支持 token.json 自动刷新）"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("⚠️ [Google Calendar] 未找到 credentials.json 文件，请检查文件名称与路径。")
                return None
            print("🔑 正在打开浏览器进行 Google 账号登录授权...")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return build('calendar', 'v3', credentials=creds)

def sync_due_claims_to_calendar():
    """每日自动运行：扫描未 Claim 的到期活动并推送到 Google Calendar"""
    print("⏰ [Google Calendar] 开始检查并同步未报销事件...")
    service = get_calendar_service()
    if not service:
        return False, "credentials.json file not found!"

    conn = get_db_connection()
    query = '''
        SELECT s.id, s.promo_end, s.split_description, s.ap_claim_case, s.amount, s.customer_invoice,
               m.brand, m.description as master_desc
        FROM promotion_splits s
        JOIN master_promotions m ON s.master_id = m.id
        WHERE s.promo_end IS NOT NULL AND s.promo_end != ''
    '''
    rows = conn.execute(query).fetchall()
    conn.close()

    today = datetime.now().date()
    synced_count = 0

    for row in rows:
        is_claimed = bool(row['ap_claim_case'] and (row['amount'] > 0 or row['customer_invoice']))
        if is_claimed:
            continue

        try:
            promo_end_date = datetime.strptime(row['promo_end'], '%Y-%m-%d').date()
        except ValueError:
            continue

        due_date = promo_end_date + timedelta(days=30)

        if today >= promo_end_date and today <= due_date:
            desc_text = row['split_description'] or row['master_desc']
            # 精确调整标题格式为：⚠️ [R Pharmacy Claim Needed] - "Description"
            summary = f'⚠️ [R Pharmacy Claim Needed] - "{desc_text}"'
            
            event_body = {
                'summary': summary,
                'description': f"Promotion ended on {row['promo_end']}.\nPlease submit A&P claim for {row['brand']} as soon as possible.",
                'start': {'date': today.strftime('%Y-%m-%d')},
                'end': {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d')},
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 9 * 60},
                    ],
                },
            }

            try:
                service.events().insert(calendarId='primary', body=event_body).execute()
                print(f"✅ 已成功发送日历提醒: {summary}")
                synced_count += 1
            except Exception as e:
                print(f"❌ 发送日历事件失败: {e}")

    return True, f"Successfully synced {synced_count} events to Google Calendar!"

# 初始化后台定时任务管理器（每日 09:00 自动执行一次）
scheduler = BackgroundScheduler()
scheduler.add_job(func=sync_due_claims_to_calendar, trigger="cron", hour=9, minute=0)
scheduler.start()

# =========================================================================
# Web API 接口
# =========================================================================

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/promotions', methods=['GET'])
def get_promotions():
    brand_filter = request.args.get('brand', '')
    search = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    
    query = '''
        SELECT DISTINCT m.* 
        FROM master_promotions m
        LEFT JOIN promotion_splits s ON m.id = s.master_id
        WHERE 1=1
    '''
    params = []
    
    if brand_filter and brand_filter != 'ALL':
        query += " AND m.brand = ?"
        params.append(brand_filter)
        
    if search:
        search_pattern = f'%{search}%'
        query += ''' AND (
            m.description LIKE ? 
            OR m.master_case_no LIKE ? 
            OR m.master_dr_no LIKE ? 
            OR s.split_description LIKE ?
            OR s.split_case_no LIKE ? 
            OR s.split_dr_no LIKE ? 
            OR s.ap_claim_case LIKE ? 
            OR s.customer_invoice LIKE ?
        )'''
        params.extend([
            search_pattern, search_pattern, search_pattern, 
            search_pattern, search_pattern, search_pattern, 
            search_pattern, search_pattern
        ])
        
    masters = conn.execute(query, params).fetchall()
    
    result = []
    total_budget = 0.0
    total_claimed = 0.0
    total_count = 0
    
    for m in masters:
        m_dict = dict(m)
        splits = conn.execute("SELECT * FROM promotion_splits WHERE master_id = ?", (m['id'],)).fetchall()
        m_dict['splits'] = [dict(s) for s in splits]
        
        total_budget += m_dict['amount_budget']
        total_claimed += sum(s['amount'] for s in m_dict['splits'])
        total_count += len(m_dict['splits']) if len(m_dict['splits']) > 0 else 1
        
        result.append(m_dict)
        
    conn.close()
    
    return jsonify({
        "data": result,
        "summary": {
            "total_budget": total_budget,
            "total_claimed": total_claimed,
            "total_count": total_count
        }
    })

@app.route('/api/promotions', methods=['POST'])
def add_promotion():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO master_promotions (brand, date, agency, description, master_case_no, master_dr_no, amount_budget, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('brand'), data.get('date'), data.get('agency'),
        data.get('description'), data.get('master_case_no'),
        data.get('master_dr_no'), data.get('amount_budget', 0.0),
        data.get('status', 'Active')
    ))
    
    master_id = cursor.lastrowid
    splits = data.get('splits', [])
    for s in splits:
        cursor.execute('''
            INSERT INTO promotion_splits (master_id, promo_start, promo_end, is_split, split_case_no, split_dr_no, split_amount, split_description, ap_claim_case, customer_invoice, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            master_id, s.get('promo_start'), s.get('promo_end'),
            s.get('is_split', 0), s.get('split_case_no'), s.get('split_dr_no'),
            s.get('split_amount', 0.0), s.get('split_description'), s.get('ap_claim_case'), s.get('customer_invoice'), s.get('amount', 0.0)
        ))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/promotions/<int:master_id>', methods=['PUT'])
def update_promotion(master_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE master_promotions 
        SET brand=?, date=?, agency=?, description=?, master_case_no=?, master_dr_no=?, amount_budget=?, status=?
        WHERE id=?
    ''', (
        data.get('brand'), data.get('date'), data.get('agency'), data.get('description'),
        data.get('master_case_no'), data.get('master_dr_no'),
        data.get('amount_budget', 0.0), data.get('status', 'Active'), master_id
    ))
    
    if data.get('promo_start') or data.get('promo_end'):
        first_split = cursor.execute("SELECT id FROM promotion_splits WHERE master_id=? ORDER BY id ASC LIMIT 1", (master_id,)).fetchone()
        if first_split:
            cursor.execute('''
                UPDATE promotion_splits 
                SET promo_start=?, promo_end=?
                WHERE id=?
            ''', (data.get('promo_start'), data.get('promo_end'), first_split['id']))

    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/promotions/<int:master_id>', methods=['DELETE'])
def delete_promotion(master_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM master_promotions WHERE id=?", (master_id,))
    cursor.execute("DELETE FROM promotion_splits WHERE master_id=?", (master_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/promotions/<int:master_id>/split', methods=['POST'])
def add_split(master_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO promotion_splits (master_id, promo_start, promo_end, is_split, split_case_no, split_dr_no, split_amount, split_description, amount)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, 0.0)
    ''', (
        master_id, 
        data.get('promo_start'), 
        data.get('promo_end'),
        data.get('split_case_no'),
        data.get('split_dr_no'),
        data.get('split_amount', 0.0),
        data.get('split_description')
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/splits/<int:split_id>', methods=['PUT'])
def update_split(split_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE promotion_splits 
        SET promo_start=?, promo_end=?, split_case_no=?, split_dr_no=?, split_amount=?, split_description=?
        WHERE id=?
    ''', (
        data.get('promo_start'), data.get('promo_end'),
        data.get('split_case_no'), data.get('split_dr_no'),
        data.get('split_amount', 0.0), data.get('split_description'),
        split_id
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/splits/<int:split_id>', methods=['DELETE'])
def delete_split(split_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM promotion_splits WHERE id=?", (split_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/splits/<int:split_id>/claim', methods=['POST'])
def update_claim(split_id):
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE promotion_splits 
        SET ap_claim_case = ?, customer_invoice = ?, amount = ?
        WHERE id = ?
    ''', (data.get('ap_claim_case'), data.get('customer_invoice'), data.get('amount', 0.0), split_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/sync-calendar', methods=['GET', 'POST'])
def trigger_sync():
    success, msg = sync_due_claims_to_calendar()
    return jsonify({"success": success, "message": msg})

if __name__ == '__main__':
    if os.path.exists('credentials.json') and not os.path.exists('token.json'):
        print("🔑 正在检查并引导 Google Calendar 登录授权...")
        try:
            get_calendar_service()
        except Exception as e:
            print(f"授权引导捕获: {e}")
            
    app.run(debug=True, port=5000)
