import streamlit as st
import pandas as pd
import sqlite3
import io
from datetime import datetime

# ==========================================
# 0. 環境套件安全性檢查與預載
# ==========================================
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

DB_NAME = "stock_notebook.db"

# ==========================================
# 1. 核心自動連動大腦：時序軸流水帳完全會計重審回寫庫存母表
# ==========================================
def sync_inventory_from_timeline(stock_id):
    """
    【數據一致性防線】
    無論操盤手做手動加減碼、CSV匯入，還是在時序軸刪除/修改歷史，
    大腦都會無條件重新清算該股所有流水帳，完美動態對齊庫存股數與動態加權成本！
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. 嚴格按時間與 ID 正序排列，精確重審每一步加權成本的演進
    cursor.execute("""
        SELECT action_type, price, shares_changed 
        FROM stock_timeline 
        WHERE stock_id=? 
        ORDER BY op_date ASC, id ASC
    """, (stock_id,))
    rows = cursor.fetchall()
    
    # 2. 精確精準鎖定當前這檔股票在 stock_master 中「持有」或最新建立的唯一 row id，防堵跨列污染
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
        # 如果時序被刪光了，庫存直接歸零並改為已結案
        cursor.execute("UPDATE stock_master SET shares=0, status='已結案' WHERE id=?", (target_master_id,))
        conn.commit()
        conn.close()
        return

    current_shares = 0.0
    current_total_cost = 0.0
    
    # 3. 完美還原馬克操盤心流：當股價歸零時，成本計量大腦自動重置（完美解決重複重入循環 Bug）
    for action, price, shares in rows:
        if action in ['初始建倉', '加碼']:
            new_shares = current_shares + shares
            if new_shares > 0:
                current_total_cost = ((current_shares * current_total_cost) + (shares * price)) / new_shares
            current_shares = new_shares
        elif action in ['減碼', '已實現出場', '手動結案']:
            current_shares = max(0.0, current_shares - shares)
            
    # 將重審後的終極會計數據覆寫回主庫存表特定行數
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
# 2. 資料庫初始化與實體月覆盤存檔系統
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立主體持股資料表
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
    
    # 欄位無損動態升級
    cursor.execute("PRAGMA table_info(stock_master)")
    existing_cols = [info[1] for info in cursor.fetchall()]
    for col, dtype in [('period', "TEXT DEFAULT '中期波段'"), 
                       ('strategy_type', "TEXT DEFAULT '2倍風險停利法'"),
                       ('stop_loss_pct', 'REAL DEFAULT 7.0'),
                       ('target_profit_pct', 'REAL DEFAULT 14.0'),
                       ('sell_ratio', 'REAL DEFAULT 50.0')]:
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE stock_master ADD COLUMN {col} {dtype}")

    # 建立歷史流水帳表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id TEXT, action_type TEXT,
            op_date TEXT, price REAL, shares_changed REAL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 建立自訂台股對照表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT, raw_name TEXT UNIQUE, yahoo_id TEXT
        )''')

    # 建立月度覆盤實體存檔表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monthly_reviews (
            ym TEXT PRIMARY KEY, review_text TEXT
        )''')
    
    cursor.execute("SELECT COUNT(*) FROM stock_mapping")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT OR IGNORE INTO stock_mapping (raw_name, yahoo_id) VALUES (?, ?)", [
            ("凱基台灣TOP50", "00922.TW"),
            ("新特", "7815.TWO")
        ])
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 3. 馬克大師理由指引庫與 Callback 狀態注入函數
# ==========================================
TEMPLATES = {
    "🟢 買入/加碼": [
        "📈 帶量突破關鍵壓力平台，趨勢轉強，順勢進場/加碼。",
        "🎯 股價拉回關鍵均線（支撐位）未破，確認止跌，分批布局。",
        "💰 看好最新財報表現與法說會展望，核心業務成長動能強勁。",
        "💵 20年長期投資計畫，固定週期系統性資本投入（定期定額/分批）。",
        "🛒 股價受大盤恐慌拖累，個股基本面未變，分批低吸攤平操作。"
    ],
    "🔴 賣出/減碼": [
        "📉 跌破重要均線/支撐位，短線趨勢轉弱，防守性減碼。",
        "🛑 觸及設定之最大停損百分比/價位，嚴格執行紀律，控制風險。",
        "🏆 股價觸及移動停利點或滿足波段目標，落袋為安，獲利出場。",
        "⚠️ 營收動能明顯轉弱/行業景氣下滑，核心買入理由消失，撤出資金。",
        "🔄 汰弱留強，將資金回收，準備轉往其他更具效益的核心資產。"
    ]
}

# 【核心修正】回呼函數：利用標準 Callback 在 Widget 渲染前完成 State 注入，防前台快取覆蓋
def set_template_text(target_key, text):
    st.session_state[target_key] = text

# 【加減碼提交回呼函數】防範 Streamlit 渲染期修改狀態引發崩潰
def submit_panel_tx_callback(db_id, sid, type_key, price_key, shares_key, date_key, note_key):
    shares_val = st.session_state.get(shares_key, 0.0)
    if shares_val <= 0:
        st.session_state[f"panel_err_{db_id}"] = "❌ 請輸入大於 0 的交易股數！"
        return
    
    act_type = st.session_state.get(type_key, "加碼")
    price_val = st.session_state.get(price_key, 0.0)
    date_val = st.session_state.get(date_key, datetime.today().date())
    note_val = st.session_state.get(note_key, "")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) 
        VALUES (?, ?, ?, ?, ?, ?)
    """, (sid, act_type, str(date_val), price_val, shares_val, note_val))
    conn.commit()
    conn.close()
    
    sync_inventory_from_timeline(sid)
    st.session_state["op_mode"][db_id] = False
    st.session_state[note_key] = ""
    if f"panel_err_{db_id}" in st.session_state: del st.session_state[f"panel_err_{db_id}"]

# 【時序軸操作回呼函數】確保更新/刪除完全重算連動庫存
def update_timeline_callback(tl_id, stock_id, act_key, date_key, pr_key, sh_key, no_key):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE stock_timeline SET action_type=?, op_date=?, price=?, shares_changed=?, note=? 
        WHERE id=?
    """, (st.session_state[act_key], st.session_state[date_key], st.session_state[pr_key], st.session_state[sh_key], st.session_state[no_key], tl_id))
    conn.commit()
    conn.close()
    sync_inventory_from_timeline(stock_id)
    st.session_state["msg_tl_global"] = "✅ 歷史流水帳修改成功，總持股庫存已全自動重新加權對齊！"

def delete_timeline_callback(tl_id, stock_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock_timeline WHERE id=?", (tl_id,))
    conn.commit()
    conn.close()
    sync_inventory_from_timeline(stock_id)
    st.session_state["msg_tl_global"] = "⚠️ 該歷史交易紀錄已註銷，總體持有庫存股數已即時動態追溯修正！"

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
# 4. 全域狀態中括號字典初始化 (徹底消滅 AttributeError)
# ==========================================
if "yahoo_prices" not in st.session_state: st.session_state["yahoo_prices"] = {}
if "edit_mode" not in st.session_state: st.session_state["edit_mode"] = {}
if "op_mode" not in st.session_state: st.session_state["op_mode"] = {}
if "csv_events" not in st.session_state: st.session_state["csv_events"] = []

tab1, tab2, tab3, tab4 = st.tabs(["📊 今日總覽", "🔍 個股時序", "📅 月覆盤", "💡 馬克心法"])

# ==========================================
# 分頁一：今日動態與資產總覽看板
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
                cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '已實現出場', ?, ?, ?, ?)", (row['stock_id'], row['op_date'], row['avg_cost'], row['shares'], f"已實現CSV自動導入，結案損益：{row['realized_pnl']}"))
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
                st.write(f"• 異動新股數：{row['shares']:,} 股 | 新均價成本：${row['avg_cost']}")
                
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
                
                csv_note_key = f"csv_note_state_key_{idx}"
                if csv_note_key not in st.session_state: st.session_state[csv_note_key] = ""
                
                cols = st.columns(2)
                for b_idx, t_text in enumerate(pool):
                    with cols[b_idx % 2]:
                        st.button(t_text[:12] + "...", key=f"csv_tpl_btn_{idx}_{b_idx}", on_click=set_template_text, args=(csv_note_key, t_text))
                            
                note_text = st.text_area("📝 6. 詳細操作理由備忘錄：", key=csv_note_key)
                
                if st.button("💾 確認寫入庫存與歷史帳", key=f"csv_save_btn_{idx}", type="primary"):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    if ev['type'] == '初始建倉':
                        cursor.execute("INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period, strategy_type, stop_loss_pct, target_profit_pct, sell_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (row['market'], sid, row['stock_name'], row['avg_cost'], row['shares'], note_text, csv_period, csv_strat, csv_sl, csv_tp, csv_ratio))
                    cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, ?, ?, ?, ?, ?)", (sid, ev['type'], op_date.strftime("%Y-%m-%d"), row['avg_cost'], abs(row['shares'] - ev.get('old_shares', 0)), note_text))
                    conn.commit()
                    conn.close()
                    sync_inventory_from_timeline(sid)
                    
                    st.session_state["csv_events"] = [e for i, e in enumerate(st.session_state["csv_events"]) if i != idx]
                    if csv_note_key in st.session_state: del st.session_state[csv_note_key]
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
                cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '初始建倉', ?, ?, ?, ?)", (m_id, datetime.today().strftime("%Y-%m-%d"), m_cost, m_shares, m_reason))
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
        st.caption("目前系統中尚無庫存數據，請先由上方或側邊欄匯入資料。")
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

            if item['alert_status'] == "stop_loss":
                st.markdown(f'''<div style="background-color: #ffebee; border-left: 8px solid #c62828; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #b71c1c;">
<b>🚨 🚨 紀律防守線觸發：已達嚴格停損點！</b><br>
<b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
核心警示：目前即時損益已跌達 <span style="font-weight:bold; font-size:1.1rem;">{profit_pct:.2f}%</span>，觸及防守門檻 (-{stock['stop_loss_pct']:.1f}%)。<br>
請立即開啟券商交易軟體，理性手起刀落執行全數停損，嚴控風險本金！
</div>''', unsafe_allow_html=True)
            elif item['alert_status'] == "take_profit":
                expected_actual_cash = round(suggested_shares * (y_price if y_price else 0), 1)
                st.markdown(f'''<div style="background-color: #ffe0b2; border-left: 8px solid #f57c00; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #5d4037;">
<b>🔥 🚨 超級績效：已達馬克分批獲利停利點！</b><br>
<b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
==========================================================<br>
💰 當 前 獲 利 ％ ： <span style="color:#d84315; font-weight:bold;">+{profit_pct:.2f}%</span> (目標: +{target_pct_val:.1f}%)<br>
🚪 門 檻 出 場 ％ ： 強制落袋 {ratio_val}% 庫存持股<br>
🛒 應 下 單 股 數 ： <b>請至券商下單賣出 【 {suggested_shares:,} 】 股</b><br>
💵 預計收回總金額 ： <b>${expected_actual_cash:,} 元</b><br>
==========================================================<br>
<small>💡 續抱指引：減碼後，系統會自動將賸餘部位的防守點移至保本價 ${stock['avg_cost']}。</small>
</div>''', unsafe_allow_html=True)

                confirm_key = f"chk_action_{db_id}"
                if st.checkbox("🧾 我已在券商完成此筆減碼下單 (勾選展開實際成交微調)", key=confirm_key):
                    with st.container(border=True):
                        st.caption("✍️ 請核對實際成交明細（容許滑價調整）：")
                        actual_shares = st.number_input("實際成交股數", value=float(suggested_shares), step=1.0, key=f"act_s_{db_id}")
                        actual_price = st.number_input("實際成交價格", value=float(y_price if y_price else take_profit_price), step=0.01, key=f"act_p_{db_id}")
                        actual_date = st.date_input("操作日期", value=datetime.today(), key=f"act_d_{db_id}")
                        
                        if st.button("👍 確定扣減股數並歸檔歷史", key=f"btn_confirm_act_{db_id}", type="primary"):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            note_msg = f"滿足馬克【{stock['strategy_type']}】({target_pct_val}%) 分批出場。實際成交價: ${actual_price} / 股數: {actual_shares}"
                            cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '減碼', ?, ?, ?, ?)", (sid, actual_date.strftime("%Y-%m-%d"), actual_price, actual_shares, note_msg))
                            conn.commit()
                            conn.close()
                            sync_inventory_from_timeline(sid)
                            if confirm_key in st.session_state: del st.session_state[confirm_key]
                            st.success("紀錄已歸帳並動態連動修正庫存！")
                            st.rerun()

            bg_color = "#e3f2fd" if stock['period'] == '長期投資' else "#ffffff"
            border_line = "6px solid #1e88e5" if stock['period'] == '長期投資' else "6px solid #757575"
            text_main_color = "#0d47a1" if stock['period'] == '長期投資' else "#333333"
            
            if y_price:
                pnl_color = "#d32f2f" if item['pnl_money'] < 0 else "#388e3c"
                pnl_arrow = "🔴" if item['pnl_money'] < 0 else "🟢"
                
                mark_guide_html = ""
                if stock['period'] != '長期投資':
                    mark_guide_html = f'''<hr style="border-top: 1px solid #e0e0e0; margin: 8px 0;">
<b>📜 馬克紀律常態操盤指引 (動態換算)：</b><br>
• ⚙️ 執行策略：【{stock['strategy_type']}】<br>
• 🛑 嚴格停損線 (-{stock['stop_loss_pct']}%): <b>${stop_loss_price}</b> (破此價強制全清倉 {stock['shares']} 股)<br>
• 🎯 分批停利點 (+{target_pct_val}%): <b>${take_profit_price}</b> (達此價強制落袋減碼 {suggested_shares} 股，預計收回 ${expected_cash} 元)'''

                stats_block = f'''<div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #424242;">
🏢 最新市價：<b>{y_price}</b> &nbsp;|&nbsp; 當前現值：<b>${item['current_value']:,.1f} 元</b><br>
即時損益：<span style="color:{pnl_color}; font-weight:bold;">{pnl_arrow} {profit_pct:.2f}%</span> &nbsp;|&nbsp; 帳面獲利提示：<span style="color:{pnl_color}; font-weight:bold;">${item['pnl_money']:,.1f} 元</span>
{mark_guide_html}
</div>'''
            else:
                stats_block = '''<div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #757575; font-style: italic;">
⚪ 帳產現值未經刷新，請點選右上角按鈕動態連動 Yahoo 股市。
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

            # 🛠️ 控制匣一：原位「加/減碼」面板 (解決套用理由失效)
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
                    
                    if tx_note_key not in st.session_state: st.session_state[tx_note_key] = ""
                    
                    cols_tx = st.columns(2)
                    for b_idx, t_text in enumerate(pool):
                        with cols_tx[b_idx % 2]:
                            st.button(t_text[:12] + "...", key=f"panel_tpl_btn_{db_id}_{b_idx}", on_click=set_template_text, args=(tx_note_key, t_text))
                                
                    st.text_area("📝 詳細操作理由：", key=tx_note_key)
                    
                    # 🎯 透過獨立的 Callback 提交交易
                    if st.button("💾 確定執行交易 (系統自動換算)", key=f"panel_tx_submit_{db_id}", type="primary", use_container_width=True, on_click=submit_panel_tx_callback, args=(db_id, sid, panel_tx_type_key, panel_tx_price_key, panel_tx_shares_key, panel_tx_date_key, tx_note_key)):
                        st.rerun()
                    if f"panel_err_{db_id}" in st.session_state and st.session_state[f"panel_err_{db_id}"]:
                        st.error(st.session_state[f"panel_err_{db_id}"])

            # 🛠️ 控制匣二：快速「細節修復編輯」面板
            if st.session_state["edit_mode"].get(db_id, False):
                with st.container(border=True):
                    st.caption(f"🔧 修正【{sid}】庫存原始設定：")
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
                    
                    if st.button("💾 儲存個股修正", key=f"save_edit_btn_{db_id}", type="primary", use_container_width=True):
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        if u_shares <= 0:
                            cursor.execute("UPDATE stock_master SET stock_id=?, stock_name=?, avg_cost=?, shares=0, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?, status='已結案' WHERE id=?", (u_id, u_name, u_cost, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, db_id))
                        else:
                            cursor.execute("UPDATE stock_master SET stock_id=?, stock_name=?, avg_cost=?, shares=?, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?, status='持有' WHERE id=?", (u_id, u_name, u_cost, u_shares, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, db_id))
                        conn.commit()
                        conn.close()
                        st.session_state["edit_mode"][db_id] = False
                        st.success("母體表格修正成功！")
                        st.rerun()
            st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)

# ==========================================
# 分頁二：個股生命週期故事書 (歷史完全連動防走位修正版)
# ==========================================
with tab2:
    st.subheader("🔍 單一個股生命週期全覆盤")
    if "msg_tl_global" in st.session_state and st.session_state["msg_tl_global"]:
        st.success(st.session_state["msg_tl_global"])
        st.session_state["msg_tl_global"] = ""

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
                st.markdown(f"🛡️ **後半段保本追蹤防守價：${df_master_info.iloc[0]['avg_cost']}** (若分批減碼成功，賫餘持股請移至此利潤保本點防守)")
            
        st.write("⏱️ 歷史操作決策流水帳 (歷史刪除修補完全連動)：")
        st.caption("🔥 【操盤防線】在下方時序中點選「刪除紀錄」或「更新紀錄」，庫存母表的總股數與均價將自動動態計量重算，不失真！")
        
        for _, row in df_timeline.iterrows():
            tl_id = row['id']
            badge = "🟢 買入/加碼" if row['action_type'] in ['初始建倉', '加碼'] else "🔴 紀律減碼/結案"
            with st.container(border=True):
                st.markdown(f"**{row['op_date']} | {badge} ({row['action_type']})**")
                st.markdown(f"成交價格: `{row['price']}` &nbsp;|&nbsp; 變動數量: `{row['shares_changed']:,} 股`")
                st.markdown(f"💬 操盤回顧日誌：\n*{row['note']}*")
                
                with st.expander("🛠️ 修改或刪除此單筆歷史大事記"):
                    act_k = f"tl_act_box_{tl_id}"
                    date_k = f"tl_d_box_{tl_id}"
                    pr_k = f"tl_pr_box_{tl_id}"
                    sh_k = f"tl_sh_box_{tl_id}"
                    no_k = f"tl_no_box_{tl_id}"
                    
                    st.selectbox("操作類別", ["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"], index=["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"].index(row['action_type']) if row['action_type'] in ["初始建倉", "加碼", "減碼", "已實現出場", "手動結案"] else 0, key=act_k)
                    st.text_input("日期 (YYYY-MM-DD)", value=row['op_date'], key=date_k)
                    st.number_input("價格", value=float(row['price']), step=0.01, key=pr_k)
                    st.number_input("變動股數", value=float(row['shares_changed']), step=1.0, key=sh_k)
                    st.text_area("日誌細節備忘", value=row['note'], key=no_k)
                    
                    col_hist_u1, col_hist_u2 = st.columns(2)
                    with col_hist_u1:
                        st.button("💾 更新紀錄", key=f"tl_submit_upd_{tl_id}", type="primary", use_container_width=True, on_click=update_timeline_callback, args=(tl_id, selected_id, act_k, date_k, pr_k, sh_k, no_k))
                    with col_hist_u2:
                        st.button("🗑️ 刪除紀錄", key=f"tl_submit_del_{tl_id}", use_container_width=True, on_click=delete_timeline_callback, args=(tl_id, selected_id))

# ==========================================
# 分頁三：月份進出場明細覆盤
# ==========================================
with tab3:
    st.subheader("📅 月度進出場決策大事記覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_dates = pd.read_sql_query("SELECT DISTINCT substr(op_date, 1, 7) as ym FROM stock_timeline ORDER BY ym DESC", conn)
    conn.close()
    
    if df_dates.empty:
        st.caption("目前尚無任何月份的操作大事記。")
    else:
        selected_ym = st.selectbox("請選擇覆盤月份：", df_dates['ym'].tolist(), key="monthly_rev_selector")
        conn = sqlite3.connect(DB_NAME)
        df_month = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE op_date LIKE '{selected_ym}%' ORDER BY op_date DESC", conn)
        
        cursor = conn.cursor()
        cursor.execute("SELECT review_text FROM monthly_reviews WHERE ym=?", (selected_ym,))
        row_rev = cursor.fetchone()
        existing_rev = row_rev[0] if row_rev else ""
        conn.close()
        
        st.write(f"### 🎯 {selected_ym} 操作紀律大事記")
        for _, row in df_month.iterrows():
            is_buy = row['action_type'] in ['初始建倉', '加碼']
            color_badge = "🟩 [買入加碼]" if is_buy else "🟥 [減碼出場]"
            st.markdown(f"**{row['op_date']}** | {color_badge} **{row['stock_id']}** (價格: ${row['price']} | 數量: {row['shares_changed']} 股)")
            st.markdown(f"└ 覆盤日誌: *{row['note']}*")
            st.markdown("---")
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("📝 交易員本月盲點與心態心流紀錄")
        user_rev = st.text_area("寫下本月的紀律執行心得與優缺點檢討：", value=existing_rev, key=f"txt_rev_area_{selected_ym}")
        
        if st.button("💾 儲存月度檢討日誌", key=f"btn_save_rev_{selected_ym}", type="primary"):
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO monthly_reviews (ym, review_text) VALUES (?, ?)", (selected_ym, user_rev))
            conn.commit()
            conn.close()
            st.success(f"{selected_ym} 交易員自我檢討心得已妥善寫入資料庫硬碟！")

# ==========================================
# 分頁四：馬克心法交易新法
# ==========================================
with tab4:
    st.markdown('''
## 💡 馬克·米奈爾維尼 (Mark Minervini) 超級績效大師心法

---
### 🎯 一、 Risks First 思維與期望值防禦 (操盤手的生命線)
* **💥 虧損控制的鐵律**
  > *「如果你不能忍受小虧損，遲早會面臨所有虧損之母。」*
  > *「輸家才會攤平輸家 (Losers average losers)！在錯誤的標不上攤平，只是在為你的傲慢支付雙倍罰款。」*
  
  馬克強調，進場前如果沒有算好停損點，就絕對不按下單鈕。本金防禦大於一切，一檔股票讓你賠掉 7%，你只需要賺 7.5% 就能回本；但如果放任虧損砍半跌掉 50%，你必須賺 100% 才能翻身。
  
* **📊 賺賠比與期望值方程式**
  > *「交易的秘密在於賺大賠小，而不是每次操作都對。只要賺賠比拉高，即使勝率只有 30%，你依然能成為富翁。」*
  
  市場上多數人著迷於尋找百分之百獲勝的聖盃，打擊率不重要，真正的期望值規模才是關鍵。讓虧損鎖在極小範圍，獲利放大到停損的 2 倍以上，資產才能真正實現複利噴發。

---
### 💰 二、 經典策略：2倍風險停利法 (不敗的數學防禦)
* **🔒 獲利的一半強制落袋**
  > *「絕不讓一筆已經大賺的利潤，演變成虧損出場。當市場已經給你兩倍風險的利賞，拿走它，落袋為安。」*
  
  當股價順利觸及你當初設定初始停損點的 2 倍（例如停損設 -7%，即時獲利來到 +14%）時，必須無條件、強制在強勢中賣出 50%（一半）的部位。
  
* **🛡️ 賸餘部位移至保本點防守**
  賣出一半鎖定利潤後：**立刻將剩下的 50% 持股停損線移到「買入成本價」**。
  此交易在數學上已立於不敗之地。最壞情況就是剩下的一半洗平手出場，但整筆單最終依然穩賺前半段的利潤！

---
### 🚀 三、 終極策略：強勢波段停利法 (Selling into Strength)
* **🛒 把股票優雅地倒給瘋狂追高的大眾**
  > *「要在強勢中賣出！不要等趨勢無情反轉、跌勢轟然啟動時，才驚慌失措地與全市場一起踩踏逃竄。」*
  
  超級飆股在多頭中，往往會在 1 到 3 週內出現垂直噴發（最後高潮 Climax Run）。此時不能等跌破均線，必須主動將 1/3 或 1/2 的籌碼在倒給瘋狂的散戶。
  
* **⚡ 觸及強勢波段停利的 4 大趕頂訊號：**
  1. **波段首波達標 (Base Hit)**：底部型態（如 VCP）帶量平台突破後快速暴漲 15% ~ 25%，此時通常會面臨首波平台拉回。
  2. **均線乖離率過大 (Extension)**：股價拋離 20 日均線超過 15%~20%，或拋離 50 日均線超過 30%，隨時會像橡皮筋裂開一樣猛烈向均線回彈。
  3. **趕頂垂直噴發 (Climax Run)**：股票連續大漲數月，末段出現角度高達 75 度以上的垂直噴發，爆出歷史天量且利多連發。
  4. **竭盡缺口與單日最大價差**：巨幅跳空開高、或出現起漲以來單日最長大紅K。
  
* **🪜 減碼後剩下的持股，該如何詳細續抱？**
  * **移至保本價**：第一時間將剩下的停損點移到買入成本價，鎖死底線。
  * **20EMA（月線）追蹤加速期**：若屬於極強勢飆股，只要每日收盤價沒有跌破 20EMA，其餘震盪一律視為噪音，死抱到底。
  * **50MA（季線）防守整理期**：強勢股拉回 50MA 是法人機構在逢低加碼，只要週K線收盤未破 50MA，賸餘部位安心續抱。
  * **階梯平台移動停損 (Backing and Filling)**：每當股價築起新整理平台並再度向上突破創新高時，將防守價逐層上移到新平台的最低點，像電梯一樣鎖死獲利。

---
### 🚨 四、 法人撤資與全面清倉結案訊號
* **💥 當大勢已去，手起刀落全面離場**
  > *「操盤手不與股票談戀愛。當大戶的腳步已經撤離，你必須比他們跑編更快。」*
  
  當股價高檔爆量跌破 50MA（季線），且隨後幾天反彈皆無力站回。這意味著主力資金正式出清，大趨勢已經終結，剩下的持股必須全數清倉、絕不留戀。

---
### 📈 五、 技術型態：VCP 波幅收窄型態 (籌碼的精準解讀)
* **🔍 尋找市場的臨界點 (Pivot Point)**
  > *「我尋找的不是便宜的低價股票，而是準備好要瘋狂飆漲的股票。」*
  
  籌碼會從散戶（軟手）轉移到主力大戶（強手）手中。表現出每次拉回修正的幅度越來越小（如：30% ➔ 15% ➔ 7% ➔ 3%），且在最右側成交量極度萎縮窒息，代表市場上想賣的人都賣光了。此時只要出現一根**帶量突破臨界點（Pivot）**的長紅K，就是最完美的初始進場一擊。
''')
