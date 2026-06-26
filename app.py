import streamlit as st
import pandas as pd
import sqlite3
import io
from datetime import datetime

# ==========================================
# 0. 全域狀態與記憶緩衝大腦初始化 (徹底封殺 KeyError)
# ==========================================
st.set_page_config(page_title="交易指揮官日誌 V5", page_icon="🛡️", layout="wide")

if "yahoo_prices" not in st.session_state:
    st.session_state["yahoo_prices"] = {}
if "edit_mode" not in st.session_state:
    st.session_state["edit_mode"] = {}
if "op_mode" not in st.session_state:
    st.session_state["op_mode"] = {}
if "csv_events" not in st.session_state:
    st.session_state["csv_events"] = []

# --- 理由模板快取記憶緩衝大腦 ---
if "tpl_buffer_dict" not in st.session_state:
    st.session_state["tpl_buffer_dict"] = {}

# --- 補齊遺失的常數字典 (修正 Bug 1) ---
TEMPLATES = {
    "🟢 買入/加碼": [
        "突破VCP收斂末端，帶量起漲",
        "拉回50MA量縮測試有撐",
        "盈餘超預期，跳空缺口支撐",
        "站上20EMA，趨勢動能轉強"
    ],
    "🔴 賣出/減碼": [
        "達標2倍停損期望值，紀律落袋",
        "高檔爆量收黑，出現竭盡缺口",
        "跌破20EMA防守線，動能轉弱",
        "跌破50MA生命線，大勢已去"
    ]
}

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

DB_NAME = "stock_notebook.db"

# ==========================================
# 1. 核心自動連動大腦：時序軸完全會計重審回寫庫存母表
# ==========================================
def sync_inventory_from_timeline(stock_id):
    """
    【數據連動一致性防線：會計重審追溯大腦】
    無條件清空母表對應數據，重新計算該股所有歷史，動態追溯對齊庫存股數與加權成本！
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT action_type, price, shares_changed 
        FROM stock_timeline 
        WHERE stock_id=? 
        ORDER BY op_date ASC, id ASC
    """, (stock_id,))
    rows = cursor.fetchall()
    
    cursor.execute("""
        SELECT id FROM stock_master 
        WHERE stock_id=? 
        ORDER BY (CASE WHEN status='持有' THEN 0 ELSE 1 END) ASC, id DESC 
        LIMIT 1
    """, (stock_id,))
    master_row = cursor.fetchone()
    if not master_row:
        conn.close()
        return
    target_master_id = master_row[0]

    if not rows:
        cursor.execute("UPDATE stock_master SET shares=0, avg_cost=0.0, status='已結案' WHERE id=?", (target_master_id,))
        conn.commit()
        conn.close()
        return

    current_shares = 0.0
    current_total_cost = 0.0
    
    for action, price, shares in rows:
        if action in ['初始建倉', '加碼']:
            new_shares = current_shares + shares
            if new_shares > 0:
                current_total_cost = ((current_shares * current_total_cost) + (shares * price)) / new_shares
            current_shares = new_shares
        elif action in ['減碼', '已實現出場', '手動結案']:
            current_shares = max(0.0, current_shares - shares)
            if current_shares == 0:
                current_total_cost = 0.0
            
    if current_shares <= 0:
        cursor.execute("UPDATE stock_master SET shares=0, status='已結案' WHERE id=?", (target_master_id,))
    else:
        cursor.execute("""
            UPDATE stock_master 
            SET shares=?, avg_cost=?, status='持有' 
            WHERE id=?
        """, (current_shares, round(current_total_cost, 2), target_master_id))
        
    conn.commit()
    conn.close()

# ==========================================
# 2. 資料庫初始化與結構升級
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT, stock_id TEXT, stock_name TEXT,
            avg_cost REAL, shares REAL, core_reason TEXT,
            period TEXT DEFAULT '中期波段', strategy_type TEXT DEFAULT '2倍風險停利法',
            stop_loss_pct REAL DEFAULT 7.0, target_profit_pct REAL DEFAULT 14.0,
            sell_ratio REAL DEFAULT 50.0, status TEXT DEFAULT '持有'
        )
    ''')
    
    cursor.execute("PRAGMA table_info(stock_master)")
    existing_master_cols = [info[1] for info in cursor.fetchall()]
    for col, dtype in [('period', "TEXT DEFAULT '中期波段'"), 
                       ('strategy_type', "TEXT DEFAULT '2倍風險停利法'"),
                       ('stop_loss_pct', 'REAL DEFAULT 7.0'),
                       ('target_profit_pct', 'REAL DEFAULT 14.0'),
                       ('sell_ratio', 'REAL DEFAULT 50.0')]:
        if col not in existing_master_cols:
            cursor.execute(f"ALTER TABLE stock_master ADD COLUMN {col} {dtype}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id TEXT, action_type TEXT,
            op_date TEXT, price REAL, shares_changed REAL, note TEXT, pnl REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("PRAGMA table_info(stock_timeline)")
    existing_timeline_cols = [info[1] for info in cursor.fetchall()]
    if 'pnl' not in existing_timeline_cols:
        cursor.execute("ALTER TABLE stock_timeline ADD COLUMN pnl REAL DEFAULT 0.0")
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_mapping (id INTEGER PRIMARY KEY AUTOINCREMENT, raw_name TEXT UNIQUE, yahoo_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS monthly_reviews (ym TEXT PRIMARY KEY, review_text TEXT)''')
    
    cursor.execute("SELECT COUNT(*) FROM stock_mapping")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT OR IGNORE INTO stock_mapping (raw_name, yahoo_id) VALUES (?, ?)", [
            ("凱基台灣TOP50", "00922.TW"), ("新特", "7815.TWO")
        ])
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 3. 理由模板一鍵緩衝套入機制 (Callback 機制)
# ==========================================
def callback_inject_template(buffer_key, selected_text):
    st.session_state["tpl_buffer_dict"][buffer_key] = selected_text

def get_custom_mappings():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT raw_name, yahoo_id FROM stock_mapping")
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def fetch_yahoo_price(stock_id, market):
    if not YFINANCE_AVAILABLE: return None
    try:
        ticker = stock_id.strip()
        if market == "台股" and ticker.isdigit(): ticker = f"{ticker}.TW"
        tick = yf.Ticker(ticker)
        df_history = tick.history(period="1d")
        if not df_history.empty: return round(df_history['Close'].iloc[-1], 2)
        return None
    except: return None

def parse_uploaded_csv(uploaded_file):
    bytes_data = uploaded_file.read()
    df = None
    for encoding in ['utf-8', 'cp950', 'big5', 'utf-8-sig']:
        try:
            df = pd.read_csv(io.BytesIO(bytes_data), encoding=encoding)
            break
        except: continue
    if df is None: return pd.DataFrame(), "未知"
    df.columns = [str(c).strip() for c in df.columns]
    parsed_data = []
    mapping_dict = get_custom_mappings()
    
    if '買賣別' in df.columns and '損益' in df.columns and '價格' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']): continue
            parsed_data.append({'market': '美股', 'stock_id': str(row['代號']).strip(), 'stock_name': str(row['股名']).strip(), 'avg_cost': float(str(row['價格']).replace(',', '')), 'shares': float(str(row['股數']).replace(',', '')), 'realized_pnl': float(str(row['損益']).replace(',', '')), 'op_date': str(row['日期']).replace('/', '-'), 'action_type': '已實現賣出'})
        return pd.DataFrame(parsed_data), "已實現"
    elif '代號' in df.columns and '均價' in df.columns and '目前庫存' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']) or str(row['代號']).strip() == '': continue
            parsed_data.append({'market': '美股', 'stock_id': str(row['代號']).strip(), 'stock_name': str(row['股票名稱']).strip(), 'avg_cost': float(str(row['均價']).replace(',', '')), 'shares': float(str(row['目前庫存']).replace(',', ''))})
        return pd.DataFrame(parsed_data), "庫存"
    elif '股票名稱' in df.columns and '成交均價' in df.columns and '股數' in df.columns:
        for _, row in df.iterrows():
            raw_name = str(row['股票名稱']).strip()
            if '總預估' in raw_name or '總融資' in raw_name or raw_name == '' or pd.isna(row['股數']): continue
            stock_id = raw_name.split(" ")[0] if " " in raw_name else raw_name
            stock_name = raw_name.split(" ", 1)[1] if " " in raw_name else raw_name
            if stock_id in mapping_dict: stock_id = mapping_dict[stock_id]
            elif stock_name in mapping_dict: stock_id = mapping_dict[stock_name]
            parsed_data.append({'market': '台股', 'stock_id': stock_id, 'stock_name': stock_name, 'avg_cost': float(str(row['成交均價']).replace(',', '')), 'shares': float(str(row['股數']).replace(',', ''))})
        return pd.DataFrame(parsed_data), "庫存"
    return pd.DataFrame(), "未知"

# ==========================================
# 4. 側邊欄常態掛載 (全系統 Excel 備份與還原工具)
# ==========================================
with st.sidebar:
    st.header("📦 系統工具與備份支援")
    if st.button("📤 導出全系統備份 Excel", use_container_width=True):
        conn = sqlite3.connect(DB_NAME)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.read_sql_query("SELECT * FROM stock_master", conn).to_excel(writer, sheet_name='Master', index=False)
            pd.read_sql_query("SELECT * FROM stock_timeline", conn).to_excel(writer, sheet_name='Timeline', index=False)
            pd.read_sql_query("SELECT * FROM stock_mapping", conn).to_excel(writer, sheet_name='Mapping', index=False)
            pd.read_sql_query("SELECT * FROM monthly_reviews", conn).to_excel(writer, sheet_name='Reviews', index=False)
        conn.close()
        st.download_button(label="💾 點擊下載備份檔", data=output.getvalue(), file_name=f"stock_backup_{datetime.today().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

    restore_file = st.file_uploader("📥 匯入歷史備份還原", type=["xlsx"])
    if restore_file and st.button("🔥 確認覆蓋還原系統", type="primary", use_container_width=True):
        try:
            excel_data = pd.read_excel(restore_file, sheet_name=None)
            conn = sqlite3.connect(DB_NAME)
            for sheet, table in [('Master', 'stock_master'), ('Timeline', 'stock_timeline'), ('Mapping', 'stock_mapping'), ('Reviews', 'monthly_reviews')]:
                if sheet in excel_data: excel_data[sheet].to_sql(table, conn, if_exists='replace', index=False)
            conn.commit()
            conn.close()
            st.success("🎉 全系統已成功還原！")
            st.rerun()
        except Exception as e: st.error(f"還原失敗: {e}")

    st.markdown("---")
    st.subheader("🔄 自訂台股 Yahoo 對照表")
    new_raw = st.text_input("券商 CSV 中文名 (如: 台積電)")
    new_yid = st.text_input("Yahoo 代號 (如: 2330.TW)")
    if st.button("➕ 儲存對照項目", use_container_width=True) and new_raw and new_yid:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO stock_mapping (raw_name, yahoo_id) VALUES (?, ?)", (new_raw, new_yid))
        conn.commit()
        conn.close()
        st.success("對照項目儲存成功！")
        st.rerun()

# ==========================================
# 5. UI 骨架建立 (五大分頁) - 修正 Bug 3
# ==========================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 今日總覽與資產", 
    "🔍 個股時序(流水帳)", 
    "📈 已實現損益(排隊)", 
    "📅 月度覆盤(存檔)", 
    "💡 馬克大師心法"
])

# ==========================================
# 分頁一：今日總覽與資產看板
# ==========================================
with tab1:
    st.subheader("📥 匯入最新券商資料")
    uploaded_file = st.file_uploader("支援上傳庫存或已實現 CSV", type=["csv"], label_visibility="collapsed")
    
    if uploaded_file:
        df_parsed, csv_type = parse_uploaded_csv(uploaded_file)
        if csv_type == "已實現":
            st.info("📊 偵測到已實現交易明細，正在自動歸帳...")
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            for _, row in df_parsed.iterrows():
                cursor.execute("UPDATE stock_master SET status='已結案' WHERE stock_id=? AND status='持有'", (row['stock_id'],))
                cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note, pnl) VALUES (?, '已實現出場', ?, ?, ?, ?, ?)", (row['stock_id'], row['op_date'], row['avg_cost'], row['shares'], f"已實現CSV自動導入", row['realized_pnl']))
            conn.commit()
            conn.close()
            st.success("🎉 已實現數據同步完畢！")
            st.rerun()
        elif csv_type == "庫存":
            conn = sqlite3.connect(DB_NAME)
            df_old = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
            conn.close()
            old_dict = {r['stock_id']: r for _, r in df_old.iterrows()}
            
            events = []
            for _, row in df_parsed.iterrows():
                sid = row['stock_id']
                if sid not in old_dict:
                    events.append({'type': '初始建倉', 'data': row})
                else:
                    old_item = old_dict[sid]
                    if abs(row['shares'] - old_item['shares']) > 0.01:
                        ev_type = '加碼' if row['shares'] > old_item['shares'] else '減碼'
                        events.append({'type': ev_type, 'data': row, 'old_shares': old_item['shares'], 'old_cost': old_item['avg_cost']})
            st.session_state["csv_events"] = events

    # 📥 【CSV 匯入互動確認匣】
    if st.session_state["csv_events"]:
        st.warning(f"⚠️ 偵測到 {len(st.session_state['csv_events'])} 筆庫存異動事件！")
        events_to_process = list(st.session_state["csv_events"])
        
        for idx, ev in enumerate(events_to_process):
            row = ev['data']
            sid = row['stock_id']
            with st.expander(f"【{ev['type']}】{sid} {row['stock_name']}", expanded=True):
                st.write(f"• 異動新股數：{row['shares']:,} 股 | 均價成本：${row['avg_cost']}")
                
                csv_period = st.radio("🏷️ 1. 投資週期分類：", ["長期投資", "中期波段", "短期操作"], index=1, key=f"csv_per_{idx}", horizontal=True)
                csv_strat = st.radio("⚙️ 2. 馬克紀律策略：", ["2倍風險停利法", "強勢波段停利法"], key=f"csv_str_{idx}", horizontal=True)
                csv_sl = st.number_input("🛡️ 3. 初始停損點 (%)：", value=7.0, step=0.5, key=f"csv_sl_{idx}")
                
                if csv_strat == "2倍風險停利法":
                    csv_tp = csv_sl * 2
                    csv_ratio = 50.0
                    st.caption(f"💡 馬克期望值連動帶入：獲利目標自動鎖定 +{csv_tp:.1f}% / 強制落袋減碼 50.0% 持股")
                else:
                    csv_tp = st.number_input("自訂目標漲幅 (%)：", value=20.0, step=1.0, key=f"csv_tp_{idx}")
                    csv_ratio = st.number_input("自訂出場持股比例 (%)：", value=33.33, step=5.0, key=f"csv_ratio_{idx}")
                    
                op_date = st.date_input("📅 4. 操作日期選單：", value=datetime.today(), key=f"csv_date_{idx}")
                
                st.caption("⚡ 5. 快速套用操作理由模板：")
                pool = TEMPLATES["🟢 買入/加碼"] if ev['type'] in ['初始建倉', '加碼'] else TEMPLATES["🔴 賣出/減碼"]
                
                csv_buf_key = f"csv_note_{idx}"
                if csv_buf_key not in st.session_state["tpl_buffer_dict"]:
                    st.session_state["tpl_buffer_dict"][csv_buf_key] = ""
                
                cols = st.columns(2)
                for b_idx, t_text in enumerate(pool):
                    with cols[b_idx % 2]:
                        st.button(t_text[:12] + "...", key=f"csv_tpl_btn_{idx}_{b_idx}", on_click=callback_inject_template, args=(csv_buf_key, t_text))
                            
                note_text = st.text_area("📝 6. 詳細操作理由備忘錄：", value=st.session_state["tpl_buffer_dict"][csv_buf_key], key=f"csv_txt_ui_{idx}")
                
                if st.button("💾 確認寫入庫存與歷史帳", key=f"csv_save_btn_{idx}", type="primary"):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    csv_calculated_pnl = 0.0
                    if ev['type'] == '初始建倉':
                        cursor.execute("INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period, strategy_type, stop_loss_pct, target_profit_pct, sell_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (row['market'], sid, row['stock_name'], row['avg_cost'], row['shares'], note_text, csv_period, csv_strat, csv_sl, csv_tp, csv_ratio))
                    elif ev['type'] == '減碼':
                        cursor.execute("SELECT avg_cost, shares FROM stock_master WHERE stock_id=? AND status='持有'", (sid,))
                        old_m = cursor.fetchone()
                        if old_m:
                            csv_calculated_pnl = round((row['avg_cost'] - old_m[0]) * abs(old_m[1] - row['shares']), 2)
                    
                    cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note, pnl) VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, ev['type'], op_date.strftime("%Y-%m-%d"), row['avg_cost'], abs(row['shares'] - ev.get('old_shares', 0)), note_text, csv_calculated_pnl))
                    conn.commit()
                    conn.close()
                    sync_inventory_from_timeline(sid)
                    
                    st.session_state["csv_events"] = [e for i, e in enumerate(st.session_state["csv_events"]) if i != idx]
                    if csv_buf_key in st.session_state["tpl_buffer_dict"]:
                        del st.session_state["tpl_buffer_dict"][csv_buf_key]
                    st.success("已成功同步寫入資料庫！")
                    st.rerun()

    with st.expander("➕ 手動新增全新個股庫存項目"):
        m_market = st.selectbox("市場", ["台股", "美股"])
        m_id = st.text_input("股票代號 (如: 00922.TW / NVDA)", key="manual_add_sid")
        m_name = st.text_input("股票名稱", key="manual_add_sname")
        m_cost = st.number_input("平均成本均價", min_value=0.0, step=0.1, key="manual_add_cost")
        m_shares = st.number_input("持有股數", min_value=0.0, step=1.0, key="manual_add_shares")
        m_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=1, key="manual_add_period")
        m_strat = st.selectbox("馬克策略", ["2倍風險停利法", "強勢波段停利法"], key="manual_add_strat")
        m_sl = st.number_input("初始停損 (%)", value=7.0, step=0.5, key="manual_add_sl")
        
        if m_strat == "2倍風險停利法":
            m_tp = m_sl * 2
            m_ratio = 50.0
            st.caption(f"💡 系統自動鎖定：期望獲利目標 {m_tp:.1f}% / 減碼比例 50%")
        else:
            m_tp = st.number_input("自訂目標漲幅 (%)", value=20.0, step=1.0, key="manual_add_tp")
            m_ratio = st.number_input("自訂出場持股比例 (%)", value=33.33, step=5.0, key="manual_add_ratio")
            
        m_reason = st.text_area("核心建倉理由", key="manual_add_reason")
        if st.button("🚀 確認手動建立新股", type="primary", use_container_width=True):
            if m_id and m_name:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period, strategy_type, stop_loss_pct, target_profit_pct, sell_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (m_market, m_id, m_name, m_cost, m_shares, m_reason, m_period, m_strat, m_sl, m_tp, m_ratio))
                cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note, pnl) VALUES (?, '初始建倉', ?, ?, ?, ?, 0.0)", (m_id, datetime.today().strftime("%Y-%m-%d"), m_cost, m_shares, m_reason))
                conn.commit()
                conn.close()
                st.success("手動庫存標的建立成功！")
                st.rerun()

    conn = sqlite3.connect(DB_NAME)
    df_db = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()

    processed_stocks = []
    if not df_db.empty:
        for _, row in df_db.iterrows():
            sid = row['stock_id']
            y_price = st.session_state["yahoo_prices"].get(sid, None)
            
            alert_status = "normal"
            profit_pct = 0.0
            current_value = 0.0
            pnl_money = 0.0
            
            if y_price and row['avg_cost'] > 0:
                profit_pct = ((y_price - row['avg_cost']) / row['avg_cost']) * 100
                current_value = y_price * row['shares']
                pnl_money = (y_price - row['avg_cost']) * row['shares']
                
                if row['period'] != '長期投資':
                    if profit_pct <= -row['stop_loss_pct']:
                        alert_status = "stop_loss"
                    else:
                        target_pct = row['stop_loss_pct'] * 2 if row['strategy_type'] == '2倍風險停利法' else row['target_profit_pct']
                        if profit_pct >= target_pct:
                            alert_status = "take_profit"
            
            sort_weight = 0 if alert_status == "stop_loss" else (1 if alert_status == "take_profit" else 2)
            processed_stocks.append({'row': row, 'y_price': y_price, 'profit_pct': profit_pct, 'current_value': current_value, 'pnl_money': pnl_money, 'alert_status': alert_status, 'sort_weight': sort_weight})
        processed_stocks.sort(key=lambda x: x['sort_weight'])

    st.markdown("---")
    col_t_title, col_t_refresh = st.columns([2, 1])
    with col_t_title: st.subheader("🟢 當前持有庫存總覽")
    with col_t_refresh:
        if st.button("🔄 刷新 Yahoo 現值", type="secondary", use_container_width=True):
            with st.spinner("即時連線抓取中..."):
                conn = sqlite3.connect(DB_NAME)
                df_query = pd.read_sql_query("SELECT stock_id, market FROM stock_master WHERE status='持有'", conn)
                conn.close()
                for _, r in df_query.iterrows():
                    p = fetch_yahoo_price(r['stock_id'], r['market'])
                    if p: st.session_state["yahoo_prices"][r['stock_id']] = p
            st.rerun()

    if not processed_stocks:
        st.caption("目前系統中尚無庫存數據，請由上方或側邊欄匯入交易資料。")
    else:
        for item in processed_stocks:
            stock = item['row']
            db_id = stock['id'] 
            sid = stock['stock_id']
            y_price = item['y_price']
            profit_pct = item['profit_pct']
            
            stop_loss_price = round(stock['avg_cost'] * (1 - (stock['stop_loss_pct'] / 100)), 2)
            target_pct_val = stock['stop_loss_pct'] * 2 if stock['strategy_type'] == '2倍風險停利法' else stock['target_profit_pct']
            ratio_val = stock['sell_ratio']
            
            take_profit_price = round(stock['avg_cost'] * (1 + (target_pct_val / 100)), 2)
            suggested_shares = round(stock['shares'] * (ratio_val / 100))
            expected_cash = round(suggested_shares * (take_profit_price), 1)

            # 🚨 卡片警示置頂
            if item['alert_status'] == "stop_loss":
                st.markdown(f'''<div style="background-color: #ffebee; border-left: 8px solid #c62828; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #b71c1c;">
<b>🛑 紀律防守線觸發：已達嚴格停損點！</b><br>
<b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
核心警示：目前即時損益已跌達 <span style="font-weight:bold; font-size:1.1rem;">{profit_pct:.2f}%</span>，觸及停損防線 (-{stock['stop_loss_pct']:.1f}%)。<br>
請立即開啟券商交易軟體，理性手起刀落執行全數停損，嚴控風險本金！
</div>''', unsafe_allow_html=True)
            elif item['alert_status'] == "take_profit":
                expected_actual_cash = round(suggested_shares * (y_price if y_price else 0), 1)
                st.markdown(f'''<div style="background-color: #ffe0b2; border-left: 8px solid #f57c00; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #5d4037;">
<b>🎯 超級績效：已達大師分批獲利停利點！</b><br>
<b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
==========================================================<br>
💰 當 前 獲 利 ％ ： <span style="color:#d84315; font-weight:bold;">+{profit_pct:.2f}%</span> (目標: +{target_pct_val:.1f}%)<br>
🚪 門 檻 出 場 ％ ： 強制落袋 {ratio_val}% 庫存持股<br>
🛒 應 下 單 股 數 ： <b>請下單賣出 【 {suggested_shares:,} 】 股</b><br>
💵 預計收回總金額 ： <b>${expected_actual_cash:,} 元</b><br>
==========================================================<br>
<small>💡 續抱指引：減碼後，大腦會自動將賸餘部位的防守點上移至保本價 ${stock['avg_cost']}。</small>
</div>''', unsafe_allow_html=True)

                confirm_key = f"chk_action_{db_id}"
                if st.checkbox("🧾 我已在券商完成此筆減碼下單 (勾選展開實際成交微調)", key=confirm_key):
                    with st.container(border=True):
                        st.caption("✍ 核對實際成交明細：")
                        actual_shares = st.number_input("實際成交股數", value=float(suggested_shares), step=1.0, key=f"act_s_{db_id}")
                        actual_price = st.number_input("實際成交價格", value=float(y_price if y_price else take_profit_price), step=0.01, key=f"act_p_{db_id}")
                        actual_date = st.date_input("操作日期", value=datetime.today(), key=f"act_d_{db_id}")
                        
                        if st.button("👍 確定扣減股數並歸檔歷史損益", key=f"btn_confirm_act_{db_id}", type="primary"):
                            calculated_pnl = round((actual_price - stock['avg_cost']) * actual_shares, 2)
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            note_msg = f"觸發馬克【{stock['strategy_type']}】分批出場。"
                            cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note, pnl) VALUES (?, '減碼', ?, ?, ?, ?, ?)", (sid, actual_date.strftime("%Y-%m-%d"), actual_price, actual_shares, note_msg, calculated_pnl))
                            conn.commit()
                            conn.close()
                            
                            sync_inventory_from_timeline(sid)
                            if confirm_key in st.session_state: del st.session_state[confirm_key]
                            st.success("紀錄已歸帳，庫存與已實現損益已動態完美追溯！")
                            st.rerun()

            bg_color = "#e3f2fd" if stock['period'] == '長期投資' else "#ffffff"
            border_line = "6px solid #1e88e5" if stock['period'] == '長期投資' else "6px solid #757575"
            text_main_color = "#0d47a1" if stock['period'] == '長期投資' else "#333333"
            
            # 卡片內部淺灰色質感高亮方塊
            if y_price:
                pnl_color = "#d32f2f" if item['pnl_money'] < 0 else "#388e3c"
                pnl_arrow = "🔴" if item['pnl_money'] < 0 else "🟢"
                
                mark_guide_html = ""
                if stock['period'] != '長期投資':
                    mark_guide_html = f'''<hr style="border-top: 1px solid #e0e0e0; margin: 8px 0;">
<b>📜 馬克紀律常態操盤指引 (大腦換算)：</b><br>
• ⚙️ 執行策略：【{stock['strategy_type']}】<br>
• 🛑 嚴格停損線 (-{stock['stop_loss_pct']}%): <b>${stop_loss_price}</b> (破此價強制全清倉 {stock['shares']} 股)<br>
• 🎯 分批停利點 (+{target_pct_val}%): <b>${take_profit_price}</b> (達此價強制落袋減碼 {suggested_shares} 股)'''

                stats_block = f'''<div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #424242;">
🏢 最新市價：<b>{y_price}</b> &nbsp;|&nbsp; 當前現值：<b>${item['current_value']:,.1f} 元</b><br>
即時損益：<span style="color:{pnl_color}; font-weight:bold;">{pnl_arrow} {profit_pct:.2f}%</span> &nbsp;|&nbsp; 帳面獲利提示：<span style="color:{pnl_color}; font-weight:bold;">${item['pnl_money']:,.1f} 元</span>
{mark_guide_html}
</div>'''
            else:
                stats_block = '''<div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #757575; font-style: italic;">
⚪ 價格未經刷新，請點選右上角按鈕動態連動 Yahoo 股市報價。
</div>'''
                
            st.markdown(f'''<div style="background-color: {bg_color}; border: 1px solid #e0e0e0; border-left: {border_line}; padding: 14px; border-radius: 6px; color: {text_main_color};">
<b style="font-size:1.1rem;">{'💠' if stock['period']=='長期投資' else '🛡️'} 【{stock['market']}】{sid} {stock['stock_name']}</b> ({stock['period']})<br>
庫存均價：<b>${stock['avg_cost']}</b> &nbsp;|&nbsp; 持有股數：<b>{stock['shares']:,} 股</b>
{stats_block}
<small>📌 核心理由：{stock['core_reason']}</small>
</div>''', unsafe_allow_html=True)

            c_op1, col_op2 = st.columns(2)
            with c_op1:
                if st.button("加/減碼", key=f"panel_op_btn_{db_id}", use_container_width=True):
                    st.session_state["op_mode"][db_id] = not st.session_state["op_mode"].get(db_id, False)
                    st.session_state["edit_mode"][db_id] = False
                    st.rerun()
            with col_op2:
                if st.button("✏️ 快速編輯", key=f"panel_edt_btn_{db_id}", use_container_width=True):
                    st.session_state["edit_mode"][db_id] = not st.session_state["edit_mode"].get(db_id, False)
                    st.session_state["op_mode"][db_id] = False
                    st.rerun()

            # 原位「加/減碼」控制面板 (一鍵快取秒套入，不再丟失)
            if st.session_state["op_mode"].get(db_id, False):
                with st.container(border=True):
                    st.caption("➕ 盤中快速【加/減碼】交易變動換算：")
                    panel_tx_type_key = f"panel_tx_type_{db_id}"
                    panel_tx_price_key = f"panel_tx_price_{db_id}"
                    panel_tx_shares_key = f"panel_tx_shares_{db_id}"
                    panel_tx_date_key = f"panel_tx_date_{db_id}"
                    tx_note_key = f"panel_note_area_{db_id}"
                    
                    st.radio("操作類別", ["加碼", "減碼"], key=panel_tx_type_key, horizontal=True)
                    st.number_input("成交單價", value=float(y_price if y_price else stock['avg_cost']), step=0.01, key=panel_tx_price_key)
                    st.number_input("交易股數", value=0.0, step=1.0, key=panel_tx_shares_key)
                    st.date_input("操作日期", value=datetime.today(), key=panel_tx_date_key)
                    
                    tx_type_curr = st.session_state.get(panel_tx_type_key, "加碼")
                    st.caption("⚡ 快速套用加減碼理由模板：")
                    pool = TEMPLATES["🟢 買入/加碼"] if tx_type_curr == "加碼" else TEMPLATES["🔴 賣出/減碼"]
                    
                    if tx_note_key not in st.session_state["tpl_buffer_dict"]:
                        st.session_state["tpl_buffer_dict"][tx_note_key] = ""
                    
                    cols_tx = st.columns(2)
                    for b_idx, t_text in enumerate(pool):
                        with cols_tx[b_idx % 2]:
                            st.button(t_text[:12] + "...", key=f"panel_tpl_btn_{db_id}_{b_idx}", on_click=callback_inject_template, args=(tx_note_key, t_text))
                                
                    tx_note = st.text_area("📝 詳細操作理由：", value=st.session_state["tpl_buffer_dict"][tx_note_key], key=f"tx_real_area_{db_id}")
                    
                    if st.button("💾 確定執行交易 (大腦動態連動)", key=f"panel_tx_submit_{db_id}", type="primary", use_container_width=True):
                        tx_sh_val = st.session_state.get(panel_tx_shares_key, 0.0)
                        if tx_sh_val <= 0:
                            st.error("請輸入大於 0 的交易股數！")
                        else:
                            tx_pr_val = st.session_state.get(panel_tx_price_key, 0.0)
                            tx_dt_val = st.session_state.get(panel_tx_date_key, datetime.today().date())
                            
                            calculated_pnl = 0.0
                            if tx_type_curr == "減碼":
                                calculated_pnl = round((tx_pr_val - stock['avg_cost']) * tx_sh_val, 2)
                                
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note, pnl) VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, tx_type_curr, str(tx_dt_val), tx_pr_val, tx_sh_val, tx_note, calculated_pnl))
                            conn.commit()
                            conn.close()
                            
                            sync_inventory_from_timeline(sid)
                            st.session_state["op_mode"][db_id] = False
                            if tx_note_key in st.session_state["tpl_buffer_dict"]:
                                del st.session_state["tpl_buffer_dict"][tx_note_key]
                            st.success("盤中變動寫入成功，大腦已動態加權校正！")
                            st.rerun()

            # 快速編輯
            if st.session_state["edit_mode"].get(db_id, False):
                with st.container(border=True):
                    st.caption(f"🔧 修正【{sid}】母體主設定：")
                    u_id = st.text_input("股票代號", value=stock['stock_id'], key=f"u_id_box_{db_id}")
                    u_name = st.text_input("股票名稱", value=stock['stock_name'], key=f"u_name_box_{db_id}")
                    u_cost = st.number_input("平均成本", value=float(stock['avg_cost']), step=0.01, key=f"u_cost_box_{db_id}")
                    u_shares = st.number_input("目前股數", value=float(stock['shares']), step=1.0, key=f"u_shares_box_{db_id}")
                    u_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=["長期投資", "中期波段", "短期操作"].index(stock['period']), key=f"u_per_box_{db_id}")
                    u_strat = st.selectbox("減碼紀律策略", ["2倍風險停利法", "強勢波段停利法"], index=["2倍風險停利法", "強勢波段停利法"].index(stock['strategy_type']), key=f"u_str_box_{db_id}")
                    u_sl = st.number_input("初始停損點 (%)", value=float(stock['stop_loss_pct']), step=0.1, key=f"u_sl_box_{db_id}")
                    
                    if u_strat == "2倍風險停利法":
                        u_tp = u_sl * 2
                        u_ratio = 50.0
                        st.caption(f"💡 策略連動：目標獲利將自動對齊為 {u_tp:.1f}% / 減碼持股 50%")
                    else:
                        u_tp = st.number_input("自訂目標漲幅 (%)", value=float(stock['target_profit_pct']), step=0.1, key=f"u_tp_box_{db_id}")
                        u_ratio = st.number_input("自訂出場持股比例 (%)", value=float(stock['sell_ratio']), step=5.0, key=f"u_rat_box_{db_id}")
                        
                    u_reason = st.text_area("核心理由", value=stock['core_reason'], key=f"u_rea_box_{db_id}")
                    
                    if st.button("💾 儲存主表格數據修正", key=f"save_edit_btn_{db_id}", type="primary", use_container_width=True):
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        if u_shares <= 0:
                            cursor.execute("UPDATE stock_master SET stock_id=?, stock_name=?, avg_cost=?, shares=0, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?, status='已結案' WHERE id=?", (u_id, u_name, u_cost, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, db_id))
                        else:
                            cursor.execute("UPDATE stock_master SET stock_id=?, stock_name=?, avg_cost=?, shares=?, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?, status='持有' WHERE id=?", (u_id, u_name, u_cost, u_shares, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, db_id))
                        conn.commit()
                        conn.close()
                        sync_inventory_from_timeline(u_id)
                        st.session_state["edit_mode"][db_id] = False
                        st.success("基本設定修改儲存成功！")
                        st.rerun()
            st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)

# ==========================================
# 分頁二：🔍 個股生命週期全覆盤 (防碰撞唯一安全鎖)
# ==========================================
with tab2:
    st.subheader("🔍 單一個股生命週期全覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_all_m = pd.read_sql_query("SELECT DISTINCT stock_id, stock_name FROM stock_master ORDER BY stock_id ASC", conn)
    conn.close()
    
    if df_all_m.empty:
        st.caption("目前系統資料庫中尚無歷史操作紀錄。")
    else:
        options = [f"{r['stock_id']} {r['stock_name']}" for _, r in df_all_m.iterrows()]
        selected_stock = st.selectbox("請選擇個股檢視生命時序：", options, key="timeline_stock_selector")
        selected_id = selected_stock.split(" ")[0]
        
        conn = sqlite3.connect(DB_NAME)
        df_timeline = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE stock_id='{selected_id}' ORDER BY op_date DESC, id DESC", conn)
        df_master_info = pd.read_sql_query(f"SELECT * FROM stock_master WHERE stock_id='{selected_id}' LIMIT 1", conn)
        conn.close()
        
        if not df_master_info.empty:
            st.info(f"💡 置頂初始核心戰略理由：\n{df_master_info.iloc[0]['core_reason']}")
            if df_master_info.iloc[0]['period'] != '長期投資':
                st.markdown(f"🛡️ **後半段保本追蹤防守價：${df_master_info.iloc[0]['avg_cost']}** (若分批減碼成功，賸餘持股請移至此成本保本點防守)")
            
        st.write("⏱️ 歷史操作決策流水帳 (歷史刪除完全連動追溯)：")
        
        # 🎯 終極防撞鎖：完全拔除迴圈 Index，純粹使用唯一的 tl_id 作為身份證 (修正 Bug 2)
        for row in df_timeline.itertuples():
            tl_id = getattr(row, 'id')
            row_action = getattr(row, 'action_type')
            row_date = getattr(row, 'op_date')
            row_price = getattr(row, 'price')
            row_shares = getattr(row, 'shares_changed')
            row_pnl = getattr(row, 'pnl', 0.0)
            row_note = getattr(row, 'note')
            
            badge = "🟢 買入/加碼" if row_action in ['初始建倉', '加碼'] else "🔴 紀律減碼"
            with st.container(border=True):
                st.markdown(f"**{row_date} | {badge} ({row_action})**")
                st.markdown(f"成交價格: `${row_price}` &nbsp;|&nbsp; 變動數量: `{row_shares:,} 股` &nbsp;|&nbsp; 實現損益金額: `${row_pnl:,}`")
                st.markdown(f"💬 操盤回顧日誌：\n*{row_note}*")
                
                with st.expander("🛠️ 修改或刪除此單筆歷史大事記"):
                    act_k = f"widget_tl_act_{tl_id}"
                    date_k = f"widget_tl_d_{tl_id}"
                    pr_k = f"widget_tl_pr_{tl_id}"
                    sh_k = f"widget_tl_sh_{tl_id}"
                    pnl_k = f"widget_tl_pnl_{tl_id}"
                    no_k = f"widget_tl_no_{tl_id}"
                    
                    st.selectbox("操作類別", ["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"], index=["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"].index(row_action) if row_action in ["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"] else 0, key=act_k)
                    st.text_input("日期 (YYYY-MM-DD)", value=row_date, key=date_k)
                    st.number_input("價格", value=float(row_price), step=0.01, key=pr_k)
                    st.number_input("變動股數", value=float(row_shares), step=1.0, key=sh_k)
                    st.number_input("實現損益金額", value=float(row_pnl), step=1.0, key=pnl_k)
                    st.text_area("日誌細節備忘", value=row_note, key=no_k)
                    
                    col_hist_u1, col_hist_u2 = st.columns(2)
                    with col_hist_u1:
                        if st.button("💾 更新此筆流水紀錄", key=f"widget_tl_submit_upd_{tl_id}", type="primary", use_container_width=True):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            cursor.execute("UPDATE stock_timeline SET action_type=?, op_date=?, price=?, shares_changed=?, pnl=?, note=? WHERE id=?", (st.session_state[act_k], st.session_state[date_k], st.session_state[pr_k], st.session_state[sh_k], st.session_state[pnl_k], st.session_state[no_k], tl_id))
                            conn.commit()
                            conn.close()
                            
                            sync_inventory_from_timeline(selected_id)
                            st.success("流水帳修改成功，母體庫存已動態更新！")
                            st.rerun()
                            
                    with col_hist_u2:
                        if st.button("🗑️ 刪除此筆交易紀錄", key=f"widget_tl_submit_del_{tl_id}", use_container_width=True):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM stock_timeline WHERE id=?", (tl_id,))
                            conn.commit()
                            conn.close()
                            
                            sync_inventory_from_timeline(selected_id)
                            st.warning("該紀錄已徹底註銷，總體持有庫存已 100% 即時自動追溯修正！")
                            st.rerun()

# ==========================================
# 分頁三：📈 已實現損益對帳單頁籤 (虧損絕對置頂排隊 + 篩選器預設本月)
# ==========================================
with tab3:
    st.subheader("📈 已實現交易損益對帳單")
    
    now_year = datetime.today().year
    now_month = datetime.today().month
    
    col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
    with col_f1:
        f_year = st.selectbox("篩選年份", ["全部"] + [str(y) for y in range(now_year-2, now_year+3)], index=3)
    with col_f2:
        f_month = st.selectbox("篩選月份", ["全部"] + [f"{m:02d}" for m in range(1, 13)], index=now_month)
    with col_f3:
        f_search = st.text_input("🔍 輸入個股名稱/代號搜尋", value="", placeholder="如: NVDA")

    conn = sqlite3.connect(DB_NAME)
    df_all_realized = pd.read_sql_query("SELECT * FROM stock_timeline WHERE action_type IN ('減碼', '已實現出場') OR pnl != 0.0", conn)
    conn.close()

    if df_all_realized.empty:
        st.caption("目前系統中尚無任何已實現損益歷史對帳紀錄。")
    else:
        df_all_realized['year'] = df_all_realized['op_date'].apply(lambda x: x.split('-')[0] if '-' in str(x) else "")
        df_all_m_str = df_all_realized['op_date'].apply(lambda x: x.split('-')[1] if '-' in str(x) and len(x.split('-'))>1 else "")
        df_all_realized['month'] = df_all_m_str.apply(lambda x: f"{int(x):02d}" if str(x).isdigit() else "")
        
        if f_year != "全部":
            df_all_realized = df_all_realized[df_all_realized['year'] == f_year]
        if f_month != "全部":
            df_all_realized = df_all_realized[df_all_realized['month'] == f_month]
        if f_search:
            df_all_realized = df_all_realized[df_all_realized['stock_id'].str.contains(f_search, case=False, na=False)]

        if df_all_realized.empty:
            st.caption("🔍 當前月份篩選條件下，無已實現的結案損益紀錄。")
        else:
            df_loss_part = df_all_realized[df_all_realized['pnl'] < 0].sort_values('pnl', ascending=True)
            df_profit_part = df_all_realized[df_all_realized['pnl'] >= 0].sort_values('pnl', ascending=False)
            df_realized_ordered = pd.concat([df_loss_part, df_profit_part])

            total_pnl_sum = df_realized_ordered['pnl'].sum()
            plus_sign = "+" if total_pnl_sum >= 0 else ""
            delta_string_val = f"{plus_sign}{total_pnl_sum:,.1f}"
            
            st.metric(label="📊 當前篩選範圍總實現損益合計", value=f"${total_pnl_sum:,.1f} 元", delta=delta_string_val)

            for r_item in df_realized_ordered.itertuples():
                pnl_val = getattr(r_item, 'pnl')
                box_style = "background-color: #ffebee; border-left: 6px solid #c62828; color: #b71c1c;" if pnl_val < 0 else "background-color: #f1f8e9; border-left: 6px solid #33691e; color: #1b5e20;"
                arrow_lbl = "🔴 虧損檢討 (正面直擊操盤痛點)" if pnl_val < 0 else "🟢 超級績效 (獲利落袋鎖定利潤)"
                
                st.markdown(f"""<div style="{box_style} border-radius: 6px; padding: 12px; margin-bottom: 8px;">
<b>{arrow_lbl} | 標的代號：{getattr(r_item, 'stock_id')}</b> ({getattr(r_item, 'op_date')})<br>
🚪 結案成交單價：${getattr(r_item, 'price')} &nbsp;|&nbsp; 減碼變動股數：{getattr(r_item, 'shares_changed'):,} 股<br>
💰 實體結算損益：<b>${pnl_val:,.1f} 元</b><br>
💬 交易理由備忘：<i>{getattr(r_item, 'note')}</i>
</div>""", unsafe_allow_html=True)

# ==========================================
# 分頁四：📅 月覆盤功能頁 (左側歷年矩陣一覽目錄 + 右側實體文字硬碟存檔)
# ==========================================
with tab4:
    st.subheader("📅 月度資產與紀律心流覆盤")
    
    col_dir_panel, col_edit_panel = st.columns([1, 2])
    
    with col_dir_panel:
        st.markdown("🗂️ **歷年 1-12 月覆盤目錄一覽**")
        
        conn = sqlite3.connect(DB_NAME)
        df_exist_reviews = pd.read_sql_query("SELECT ym, review_text FROM monthly_reviews ORDER BY ym DESC", conn)
        conn.close()
        exist_review_map = {r['ym']: r['review_text'] for _, r in df_exist_reviews.iterrows()}
        
        select_review_year = st.selectbox("選擇調閱年份", [str(datetime.today().year), str(datetime.today().year-1)], index=0, key="dir_review_year_set")
        
        if "active_review_ym" not in st.session_state:
            st.session_state["active_review_ym"] = datetime.today().strftime("%Y-%m")

        for m in range(1, 13):
            target_ym_str = f"{select_review_year}-{m:02d}"
            has_data = target_ym_str in exist_review_map
            lbl = f"📅 {m:02d} 月份日誌 " + ("(🟢 已存檔)" if has_data else "(⚪ 空白)")
            
            if st.button(lbl, key=f"dir_month_btn_{target_ym_str}", use_container_width=True):
                st.session_state["active_review_ym"] = target_ym_str

    with col_edit_panel:
        active_ym = st.session_state["active_review_ym"]
        st.markdown(f"✍️ **正在編輯與永續調閱月份：`{active_ym}`**")
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT review_text FROM monthly_reviews WHERE ym=?", (active_ym,))
        db_review_row = cursor.fetchone()
        conn.close()
        
        current_db_review_text = db_review_row[0] if db_review_row else ""
        
        txt_review_input = st.text_area("交易員自我核心檢討與盲點覆盤：", value=current_db_review_text, key=f"real_review_editor_key_{active_ym}", height=300)
        
        if st.button("💾 永久儲存此月度檢討日誌", key=f"save_review_btn_{active_ym}", type="primary", use_container_width=True):
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO monthly_reviews (ym, review_text) VALUES (?, ?)", (active_ym, txt_review_input))
            conn.commit()
            conn.close()
            st.success(f"🎉 成功！{active_ym} 交易紀律檢討與心流日誌已真實存檔至 SQLite 資料庫硬碟！")
            st.rerun()

# ==========================================
# 分頁五：💡 馬克心法交易新法 (原生安全多行渲染)
# ==========================================
with tab5:
    st.markdown("## 💡 馬克·米奈爾維尼 (Mark Minervini) 超級績效大師心法")
    st.markdown("---")
    
    st.markdown("### 🎯 一、 Risks First 思維與期望值防禦 (操盤手的生命線)")
    st.markdown("• **虧損控制的鐵律**")
    st.caption("「如果你不能忍受小虧損，遲早會面臨所有虧損之母。」")
    st.caption("「輸家才會攤平輸家 (Losers average losers)！在錯誤的標的上方分批低吸攤平，只是在為你的傲慢與不願認錯支付雙倍罰款。」")
    st.markdown("馬克強調，進場前如果沒有算好精確停損點，就絕對不按下單鈕。本金防禦大於一切，一檔股票讓你賠掉 7%，你只需要賺 7.5% 就能回本；但如果放任虧損砍半跌掉 50%，你必須大賺 100% 才能翻身。")
    
    st.markdown("• **賺賠比與期望值方程式**")
    st.caption("「交易的秘密在於賺大賠小，而不是每次操作都對。只要賺賠比拉高，即使勝率只有 30%，你依然能成為富翁。」")
    st.markdown("市場上多數人著迷於尋找百分之百獲勝的聖盃，打擊率完全不重要，真正的期望值規模賺賠比才是關鍵。讓每一筆虧損鎖在極小範圍，獲利放大到停損的 2 倍以上，資產才能真正實現滾動複利噴發。")

    st.markdown("---")
    st.markdown("### 💰 二、 經典策略：2倍風險停利法 (不敗的數學防禦)")
    st.markdown("• **獲利的一半強制落袋**")
    st.caption("「絕不讓一筆已經大賺的利潤，演變成虧損出場。當市場已經給你兩倍風險的利潤，拿走它，落袋為安。」")
    st.markdown("當股價順利觸及你當初設定初始停損點的 2 倍（例如停損設 -7%，即時獲利來到 +14%）時，必須毫無懸念、開除情緒，**無條件、強制在強勢中賣出 50%（一半）的持股部位**。")
    st.markdown("• **賸餘部位移至保本點防守**")
    st.markdown("賣出一半鎖定利潤後，最重要的關鍵動作：**立刻將剩下的 50% 持股停損線往上移到你的「買入成本價」**。此交易在數學上已立於不敗之地。最壞情況就是剩下的一半洗平手出場，但整筆單最終依然穩賺前半段的利潤！永遠不准讓大賺的部位變賠錢。")

    st.markdown("---")
    st.markdown("### 🚀 三、 終極策略：強勢波段停利法 (Selling into Strength)")
    st.markdown("• **把股票優雅地倒給瘋狂追高的大眾**")
    st.caption("「要在強勢中賣出！不要等趨勢無情反轉、跌勢轟然啟動時，才驚慌失措地與全市場一起踩踏逃竄。」")
    st.markdown("超級飆股在多頭中，往往會在 1 到 3 週內出現垂直噴發（最後高潮 Climax Run）。此時不能等跌破均線，必須主動將 1/3 或 1/2 的籌碼在最瘋狂狂熱、利多連發的一兩天內「主動賣在強勢中」，優雅把獲利放進口袋。")
    st.markdown("• **觸及強勢波段停利的 4 大趕頂訊號：**")
    st.markdown("1. **波段首波達標 (Base Hit)**：底部型態（如 VCP）帶量平台突破後快速暴漲 15% ~ 25%，此時通常會面臨首波平台拉回。")
    st.markdown("2. **均線乖離率過大 (Extension)**：股價拋離 20 日均線超過 15%~20%，或拋離 50 日均線超過 30%，隨時會像橡皮筋裂開一樣猛烈向均線回彈。")
    st.markdown("3. **趕頂垂直噴發 (Climax Run)**：股票連續大漲數月，末段出現角度高達 75 度以上的垂直噴發，爆出歷史天量且媒體、市場集體發瘋吹捧。")
    st.markdown("4. **竭盡缺口與單日最大價差**：長途跋涉後出現巨幅跳空缺口、或出現起漲以來單日實體K棒最長、漲幅最大的一天。")
    st.markdown("• **減碼後剩下的持股，該如何詳細續抱捕捉翻倍大浪？**")
    st.markdown("- **移至保本價**：第一時間將剩下的持股停損點上移到買入成本價，鎖死底線。")
    st.markdown("- **20EMA（月線）追蹤加速期**：若屬於最瘋狂的垂直噴發期，只要每日收盤價沒有跌破 20EMA，其餘震盪一律視為噪音，持股死抱到底。")
    st.markdown("- **50MA（季線）防守整理期**：強勢股拉回第一個或第二個平台橫盤是法人機構逢低進場。只要週K線收盤未破 50MA，賸餘部位安心續抱。")
    st.markdown("- **階梯平台移動停損 (Backing and Filling)**：每當股價成功築起新橫盤整理平台（Base 2）並再度向上突破創新高時，將防守價逐層上移到新平台的最低點，像電梯一樣階梯式鎖死獲利。")

    st.markdown("---")
    st.markdown("### 🚨 四、 法人撤資與全面清倉結案訊號")
    st.markdown("• **當大勢已去，手起刀落全面離場**")
    st.caption("「操盤手不與股票談戀愛。當大戶的腳步已經撤離，你必須比他們跑得更快。」")
    st.markdown("當一檔個股在高檔爆量跌破 50MA（季線），且隨後幾天連續反彈皆無力站回 50MA，代表主力資金已經撤資出清完畢。這意味多頭大趨勢已經終結，剩下的部位必須手起刀落全數清倉結案、絕不留戀。")

    st.markdown("---")
    st.markdown("### 📈 五、 技術型態：VCP 波幅收窄型態 (籌碼的精準解讀)")
    st.markdown("• **尋找市場的臨界點 (Pivot Point)**")
    st.caption("「我尋找的不是便宜的低價股票，而是準備好要瘋狂飆漲的股票。」")
    st.markdown("超級飆股在噴發前，籌碼會從散戶（軟手）轉移到主力大戶（強手）手中。在K線型態上會表現出每次拉回修正的幅度越來越小（例如：30% ➔ 15% ➔ 7% ➔ 3%），且在最右側成交量極度萎縮窒息，代表市場上想賣的人都賣光了。此時只要出現一根**順勢帶量突破臨界點（Pivot Point）**的長紅K，就是最完美的第一筆初始進場建倉時機。")
