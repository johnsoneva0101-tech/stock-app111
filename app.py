import streamlit as st
import pandas as pd
import sqlite3
import io
from datetime import datetime

# 嘗試匯入 yfinance 抓取即時價格
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ==========================================
# 🎯 台股中文名稱自動對照表 (上傳時自動轉換為 Yahoo 代號)
# ==========================================
TAIWAN_STOCK_MAP = {
    "凱基台灣TOP50": "00922.TW",
    "新特": "7815.TWO",
}

DB_NAME = "stock_notebook.db"

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

# ==========================================
# 2. 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT,
            stock_id TEXT,
            stock_name TEXT,
            avg_cost REAL,
            shares REAL,
            core_reason TEXT,
            status TEXT DEFAULT '持有'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT,
            action_type TEXT,
            op_date TEXT,
            price REAL,
            shares_changed REAL,
            tag TEXT,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 3. Yahoo 股市即時價格抓取
# ==========================================
def fetch_yahoo_price(stock_id, market):
    if not YFINANCE_AVAILABLE:
        return None
    try:
        ticker = stock_id.strip()
        if market == "台股" and ticker.isdigit():
            ticker = f"{ticker}.TW"
        
        tick = yf.Ticker(ticker)
        df_history = tick.history(period="1d")
        if not df_history.empty:
            return round(df_history['Close'].iloc[-1], 2)
        return None
    except:
        return None

# ==========================================
# 4. 券商 CSV 解析邏輯
# ==========================================
def parse_uploaded_csv(uploaded_file):
    bytes_data = uploaded_file.read()
    df = None
    for encoding in ['utf-8', 'cp950', 'big5', 'utf-8-sig']:
        try:
            df = pd.read_csv(io.BytesIO(bytes_data), encoding=encoding)
            break
        except:
            continue
    if df is None:
        return pd.DataFrame(), "未知"
        
    df.columns = [str(c).strip() for c in df.columns]
    parsed_data = []
    
    # ─── 類型一：複委託已實現 CSV ───
    if '買賣別' in df.columns and '損益' in df.columns and '價格' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']): continue
            parsed_data.append({
                'market': '美股',
                'stock_id': str(row['代號']).strip(),
                'stock_name': str(row['股名']).strip(),
                'avg_cost': float(str(row['價格']).replace(',', '')),
                'shares': float(str(row['股數']).replace(',', '')),
                'realized_pnl': float(str(row['損益']).replace(',', '')),
                'op_date': str(row['日期']).replace('/', '-'),
                'action_type': '已實現賣出'
            })
        return pd.DataFrame(parsed_data), "已實現"

    # ─── 類型二：美股複委託庫存 CSV ───
    elif '代號' in df.columns and '均價' in df.columns and '目前庫存' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']) or str(row['代號']).strip() == '': continue
            parsed_data.append({
                'market': '美股', 
                'stock_id': str(row['代號']).strip(),
                'stock_name': str(row['股票名稱']).strip(),
                'avg_cost': float(str(row['均價']).replace(',', '')),
                'shares': float(str(row['目前庫存']).replace(',', ''))
            })
        return pd.DataFrame(parsed_data), "庫存"
        
    # ─── 類型三：台股未實現彙總 CSV ───
    elif '股票名稱' in df.columns and '成交均價' in df.columns and '股數' in df.columns:
        for _, row in df.iterrows():
            raw_name = str(row['股票名稱']).strip()
            if '總預估' in raw_name or '總融資' in raw_name or raw_name == '' or pd.isna(row['股數']): 
                continue
            
            stock_id = raw_name.split(" ")[0] if " " in raw_name else raw_name
            stock_name = raw_name.split(" ", 1)[1] if " " in raw_name else raw_name
            
            # 自動對照轉換為 Yahoo 專用代號
            if stock_id in TAIWAN_STOCK_MAP:
                stock_id = TAIWAN_STOCK_MAP[stock_id]
            elif stock_name in TAIWAN_STOCK_MAP:
                stock_id = TAIWAN_STOCK_MAP[stock_name]
            
            shares_val = float(str(row['股數']).replace(',', ''))
            cost_val = float(str(row['成交均價']).replace(',', ''))
            
            parsed_data.append({
                'market': '台股', 
                'stock_id': stock_id, 
                'stock_name': stock_name,
                'avg_cost': cost_val,
                'shares': shares_val
            })
        return pd.DataFrame(parsed_data), "庫存"
        
    return pd.DataFrame(), "未知"

# ==========================================
# 5. 庫存比對引擎
# ==========================================
def check_inventory_changes(df_new):
    if df_new.empty: return []
    conn = sqlite3.connect(DB_NAME)
    df_old = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()
    
    events = []
    old_dict = {row['stock_id']: row for _, row in df_old.iterrows()}
    new_stock_ids = set()
    
    for _, row in df_new.iterrows():
        sid = row['stock_id']
        new_stock_ids.add(sid)
        if sid not in old_dict:
            events.append({
                'type': '初始建倉', 'market': row['market'], 'stock_id': sid, 'stock_name': row['stock_name'],
                'old_shares': 0, 'new_shares': row['shares'], 'old_cost': 0, 'new_cost': row['avg_cost']
            })
        else:
            old_item = old_dict[sid]
            if abs(row['shares'] - old_item['shares']) > 0.001:
                event_type = '加碼' if row['shares'] > old_item['shares'] else '減碼'
                events.append({
                    'type': event_type, 'market': old_item['market'], 'stock_id': sid, 'stock_name': old_item['stock_name'],
                    'old_shares': old_item['shares'], 'new_shares': row['shares'],
                    'old_cost': old_item['avg_cost'], 'new_cost': row['avg_cost']
                })
    for sid, old_item in old_dict.items():
        if sid not in new_stock_ids:
            events.append({
                'type': '全數賣出', 'market': old_item['market'], 'stock_id': sid, 'stock_name': old_item['stock_name'],
                'old_shares': old_item['shares'], 'new_shares': 0, 'old_cost': old_item['avg_cost'], 'new_cost': 0
            })
    return events

# ==========================================
# 6. UI 介面設計 (補回漏掉的關鍵手機分頁宣告)
# ==========================================
st.set_page_config(page_title="策略筆記本", layout="centered")
st.title("📱 投資決策筆記本")

# 💡 關鍵修復：宣告手機版分頁元件
tab1, tab2, tab3 = st.tabs(["📊 今日動態/總覽", "🔍 個股時序", "📅 月份回顧"])

if "yahoo_prices" not in st.session_state:
    st.session_state.yahoo_prices = {}

# ------------------------------------------
# 分頁一：今日動態與總覽
# ------------------------------------------
with tab1:
    st.subheader("📥 匯入最新 CSV 檔案")
    uploaded_file = st.file_uploader("支援上傳庫存或已實現 CSV", type=["csv"], label_visibility="collapsed")
    
    if uploaded_file:
        df_parsed, csv_type = parse_uploaded_csv(uploaded_file)
        
        if csv_type == "已實現":
            st.success(f"📈 成功識別已實現明細！共 {len(df_parsed)} 筆交易，換算封存中...")
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            for _, row in df_parsed.iterrows():
                cursor.execute("UPDATE stock_master SET status='已結案' WHERE stock_id=? AND status='持有'", (row['stock_id'],))
                cursor.execute("""
                    INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                    VALUES (?, '已實現出場', ?, ?, ?, ?)
                """, (row['stock_id'], row['op_date'], row['avg_cost'], row['shares'], f"系統自動從已實現CSV換算導入。實現損益：{row['realized_pnl']}"))
            conn.commit()
            conn.close()
            st.success("🎉 已實現資料換算完畢！已同步移出當前庫存。")
            st.rerun()
            
        elif csv_type == "庫存":
            events = check_inventory_changes(df_parsed)
            if not events:
                st.info("💡 庫存數據與上次相同，無變動事件。")
            else:
                st.warning(f"⚠️ 偵測到 {len(events)} 筆庫存變動事件！")
                for idx, ev in enumerate(events):
                    with st.expander(f"【{ev['type']}】{ev['stock_id']} {ev['stock_name']}", expanded=True):
                        st.write(f"• 股數：{ev['old_shares']} ➔ {ev['new_shares']}")
                        st.write(f"• 均價：${ev['old_cost']} ➔ ${ev['new_cost']}")
                        
                        op_date = st.date_input("📅 操作日期", value=datetime.today(), key=f"date_{idx}")
                        op_date_str = op_date.strftime("%Y-%m-%d")
                        
                        pool = TEMPLATES["🟢 買入/加碼"] if ev['type'] in ['初始建倉', '加碼'] else TEMPLATES["🔴 賣出/減碼"]
                        input_key = f"note_{idx}"
                        if input_key not in st.session_state: st.session_state[input_key] = ""
                        
                        cols = st.columns(2)
                        for b_idx, template_text in enumerate(pool):
                            with cols[b_idx % 2]:
                                if st.button(template_text[:12] + "...", key=f"btn_{idx}_{b_idx}"):
                                    st.session_state[input_key] = template_text
                                
                        note_text = st.text_area("📝 詳細理由：", value=st.session_state[input_key], key=input_key)
                        
                        if st.button("💾 確認寫入歷史流水帳", key=f"save_{idx}", type="primary"):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            shares_diff = abs(ev['new_shares'] - ev['old_shares'])
                            
                            cursor.execute("""
                                INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (ev['stock_id'], ev['type'], op_date_str, ev['new_cost'], shares_diff, note_text))
                            
                            if ev['type'] == '初始建倉':
                                cursor.execute("""
                                    INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, status)
                                    VALUES (?, ?, ?, ?, ?, ?, '持有')
                                """, (ev['market'], ev['stock_id'], ev['stock_name'], ev['new_cost'], ev['new_shares'], note_text))
                            elif ev['type'] in ['加碼', '減碼']:
                                cursor.execute("""
                                    UPDATE stock_master SET avg_cost = ?, shares = ? WHERE stock_id = ? AND status = '持有'
                                """, (ev['new_cost'], ev['new_shares'], ev['stock_id']))
                            elif ev['type'] == '全數賣出':
                                cursor.execute("UPDATE stock_master SET status = '已結案' WHERE stock_id = ? AND status = '持有'", (ev['stock_id'],))
                                
                            conn.commit()
                            conn.close()
                            st.success(f"【{ev['stock_id']}】同步成功！")
                            st.rerun()
        else:
            st.error("無法辨識此 CSV 檔案架構。")

    # ─── 手動新增與微調區塊 ───
    st.markdown("---")
    with st.expander("➕ 手動新增 / 微調修改個股資料"):
        m_market = st.selectbox("市場", ["台股", "美股"])
        m_id = st.text_input("股票代號 (如: 00922.TW / NVDA)", key="manual_id")
        m_name = st.text_input("股票名稱", key="manual_name")
        m_cost = st.number_input("平均成本", min_value=0.0, step=0.1, key="manual_cost")
        m_shares = st.number_input("目前總股數", min_value=0.0, step=1.0, key="manual_shares")
        m_reason = st.text_area("初始核心理由 / 修改備註", key="manual_reason")
        
        if st.button("🚀 儲存變更至庫存", type="primary"):
            if m_id and m_name:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM stock_master WHERE stock_id = ? AND status = '持有'", (m_id,))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE stock_master SET avg_cost = ?, shares = ?, core_reason = ?, market = ?, stock_name = ?
                        WHERE stock_id = ? AND status = '持有'
                    """, (m_cost, m_shares, m_reason, m_market, m_name, m_id))
                    st.success(f"已更新 {m_id} 的持股與理由！")
                else:
                    cursor.execute("""
                        INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, status)
                        VALUES (?, ?, ?, ?, ?, ?, '持有')
                    """, (m_market, m_id, m_name, m_cost, m_shares, m_reason))
                    st.success(f"已手動建立全新標的 {m_id}！")
                conn.commit()
                conn.close()
                st.rerun()

    # ─── 庫存總覽卡片區 (Yahoo 連動) ───
    st.markdown("---")
    col_title, col_refresh = st.columns([2, 1])
    with col_title:
        st.subheader("🟢 當前持有庫存總覽")
    with col_refresh:
        if st.button("🔄 刷新 Yahoo 現值", type="secondary", use_container_width=True):
            with st.spinner("正在連線 Yahoo 股市..."):
                conn = sqlite3.connect(DB_NAME)
                df_temp = pd.read_sql_query("SELECT stock_id, market FROM stock_master WHERE status='持有'", conn)
                conn.close()
                for _, r in df_temp.iterrows():
                    p = fetch_yahoo_price(r['stock_id'], r['market'])
                    if p: st.session_state.yahoo_prices[r['stock_id']] = p
            st.rerun()

    conn = sqlite3.connect(DB_NAME)
    df_masters = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()
    
    if df_masters.empty:
        st.caption("目前系統中尚無持股資料。")
    else:
        for _, row in df_masters.iterrows():
            with st.container(border=True):
                sid = row['stock_id']
                y_price = st.session_state.yahoo_prices.get(sid, None)
                
                if y_price and row['avg_cost'] > 0:
                    pnl_rate = ((y_price - row['avg_cost']) / row['avg_cost']) * 100
                    current_value = y_price * row['shares']
                    pnl_money = (y_price - row['avg_cost']) * row['shares']
                    
                    color_tag = "🔴" if pnl_money < 0 else "🟢"
                    pnl_str = f"{color_tag} 即時損益率: **{pnl_rate:.2f}%** (損益: {pnl_money:,.1f})"
                    value_str = f"📈 Yahoo市價: **{y_price}** | 當前現值: **{current_value:,.1f}**"
                else:
                    pnl_str = "⚪ 點擊上方刷新以獲取即時損益率"
                    value_str = "現值估算：未刷新價格"
                
                st.markdown(f"**[{row['market']}] {sid} {row['stock_name']}**")
                st.markdown(f"成本均價: `{row['avg_cost']}` | 持有股數: `{row['shares']}`")
                st.markdown(value_str)
                st.markdown(pnl_str)
                st.caption(f"📌 核心理由: {row['core_reason']}")
                
                if st.button("手動結案/移出", key=f"del_{sid}", use_container_width=True):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_master SET status = '已結案' WHERE stock_id = ? AND status = '持有'", (sid,))
                    conn.commit()
                    conn.close()
                    st.success(f"{sid} 已從庫存移除。")
                    st.rerun()

# ------------------------------------------
# 分頁二：個股時序
# ------------------------------------------
with tab2:
    st.subheader("🔍 單一個股完整時序")
    conn = sqlite3.connect(DB_NAME)
    df_all_m = pd.read_sql_query("SELECT stock_id, stock_name FROM stock_master", conn)
    conn.close()
    if df_all_m.empty:
        st.caption("尚無任何股票歷史資料。")
    else:
        options = [f"{r['stock_id']} {r['stock_name']}" for _, r in df_all_m.drop_duplicates().iterrows()]
        selected_stock = st.selectbox("請選擇個股檢視：", options)
        selected_id = selected_stock.split(" ")[0]
        
        conn = sqlite3.connect(DB_NAME)
        df_timeline = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE stock_id='{selected_id}' ORDER BY op_date DESC", conn)
        df_m_info = pd.read_sql_query(f"SELECT core_reason FROM stock_master WHERE stock_id='{selected_id}' LIMIT 1", conn)
        conn.close()
        
        if not df_m_info.empty:
            st.info(f"💡 置頂核心戰略母體理由：\n{df_m_info.iloc[0]['core_reason']}")
        for _, row in df_timeline.iterrows():
            color = "🟢" if "買" in row['action_type'] or "加" in row['action_type'] else "🔴"
            with st.container(border=True):
                st.markdown(f"{color} **{row['op_date']} | {row['action_type']}**")
                st.write(f"參考價格: `${row['price']}` | 變動股數: `{row['shares_changed']}`")
                st.markdown(f"💬 **理由**: {row['note']}")

# ------------------------------------------
# 分頁三：月份回顧
# ------------------------------------------
with tab3:
    st.subheader("📅 月份進出場明細覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_dates = pd.read_sql_query("SELECT DISTINCT substr(op_date, 1, 7) as ym FROM stock_timeline ORDER BY ym DESC", conn)
    conn.close()
    if df_dates.empty:
        st.caption("目前尚無任何月份的操作數據。")
    else:
        selected_ym = st.selectbox("請選擇回顧月份：", df_dates['ym'].tolist())
        conn = sqlite3.connect(DB_NAME)
        df_month = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE op_date LIKE '{selected_ym}%' ORDER BY op_date DESC", conn)
        conn.close()
        st.write(f"### 🎯 {selected_ym} 操作大事記")
        for _, row in df_month.iterrows():
            badge = "🟩 [買入/動態]" if "買" in row['action_type'] or "加" in row['action_type'] else "🟥 [賣出/出場]"
            st.markdown(f"**{row['op_date']}** | {badge} **{row['stock_id']}**")
            st.markdown(f"└ 訊息/理由: *{row['note']}*")
            st.markdown("---")
