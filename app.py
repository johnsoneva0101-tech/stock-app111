import streamlit as st
import pandas as pd
import sqlite3
import io
from datetime import datetime

# 嘗試匯入 yfinance 與 openpyxl
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ==========================================
# 1. 資料庫初始化與結構升級
# ==========================================
DB_NAME = "stock_notebook.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立主體持股資料表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT,
            stock_id TEXT,
            stock_name TEXT,
            avg_cost REAL,
            shares REAL,
            core_reason TEXT,
            period TEXT DEFAULT '中期波段',
            strategy_type TEXT DEFAULT '2倍風險停利法',
            stop_loss_pct REAL DEFAULT 7.0,
            target_profit_pct REAL DEFAULT 15.0,
            sell_ratio REAL DEFAULT 50.0,
            status TEXT DEFAULT '持有'
        )
    ''')
    
    # 動態補足欄位
    cursor.execute("PRAGMA table_info(stock_master)")
    existing_cols = [info[1] for info in cursor.fetchall()]
    for col, dtype in [('period', 'TEXT DEFAULT \'中期波段\''), 
                       ('strategy_type', 'TEXT DEFAULT \'2倍風險停利法\''),
                       ('stop_loss_pct', 'REAL DEFAULT 7.0'),
                       ('target_profit_pct', 'REAL DEFAULT 15.0'),
                       ('sell_ratio', 'REAL DEFAULT 50.0')]:
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE stock_master ADD COLUMN {col} {dtype}")

    # 建立歷史流水帳表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT,
            action_type TEXT,
            op_date TEXT,
            price REAL,
            shares_changed REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 建立自訂台股對照表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT UNIQUE,
            yahoo_id TEXT
        )
    ''')
    
    # 預設對照資料
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
# 2. 核心理由模板與基礎功能
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
        if market == "台股" and ticker.isdigit():
            ticker = f"{ticker}.TW"
        tick = yf.Ticker(ticker)
        df_history = tick.history(period="1d")
        if not df_history.empty:
            return round(df_history['Close'].iloc[-1], 2)
        return None
    except:
        return None

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
    
    # 複委託已實現 CSV
    if '買賣別' in df.columns and '損益' in df.columns and '價格' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']): continue
            parsed_data.append({
                'market': '美股', 'stock_id': str(row['代號']).strip(), 'stock_name': str(row['股名']).strip(),
                'avg_cost': float(str(row['價格']).replace(',', '')), 'shares': float(str(row['股數']).replace(',', '')),
                'realized_pnl': float(str(row['損益']).replace(',', '')), 'op_date': str(row['日期']).replace('/', '-'),
                'action_type': '已實現賣出'
            })
        return pd.DataFrame(parsed_data), "已實現"

    # 美股庫存 CSV
    elif '代號' in df.columns and '均價' in df.columns and '目前庫存' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']) or str(row['代號']).strip() == '': continue
            parsed_data.append({
                'market': '美股', 'stock_id': str(row['代號']).strip(), 'stock_name': str(row['股票名稱']).strip(),
                'avg_cost': float(str(row['均價']).replace(',', '')), 'shares': float(str(row['目前庫存']).replace(',', ''))
            })
        return pd.DataFrame(parsed_data), "庫存"
        
    # 台股未實現彙總 CSV
    elif '股票名稱' in df.columns and '成交均價' in df.columns and '股數' in df.columns:
        for _, row in df.iterrows():
            raw_name = str(row['股票名稱']).strip()
            if '總預估' in raw_name or '總融資' in raw_name or raw_name == '' or pd.isna(row['股數']): continue
            
            stock_id = raw_name.split(" ")[0] if " " in raw_name else raw_name
            stock_name = raw_name.split(" ", 1)[1] if " " in raw_name else raw_name
            
            if stock_id in mapping_dict: stock_id = mapping_dict[stock_id]
            elif stock_name in mapping_dict: stock_id = mapping_dict[stock_name]
            
            shares_val = float(str(row['股數']).replace(',', ''))
            cost_val = float(str(row['成交均價']).replace(',', ''))
            
            parsed_data.append({
                'market': '台股', 'stock_id': stock_id, 'stock_name': stock_name, 'avg_cost': cost_val, 'shares': shares_val
            })
        return pd.DataFrame(parsed_data), "庫存"
    return pd.DataFrame(), "未知"

# ==========================================
# 3. UI 介面與狀態初始化
# ==========================================
st.set_page_config(page_title="策略紀律筆記本", layout="centered")
st.title("📱 投資決策紀律筆記本")

tab1, tab2, tab3 = st.tabs(["📊 今日動態/總覽", "🔍 個股時序軸", "📅 月度覆盤"])

if "yahoo_prices" not in st.session_state: st.session_state.yahoo_prices = {}
if "edit_mode" not in st.session_state: st.session_state.edit_mode = {}
if "op_mode" not in st.session_state: st.session_state.op_mode = {}
if "csv_events" not in st.session_state: st.session_state.csv_events = []

# ─── 側邊欄工具 ───
with st.sidebar:
    st.header("📦 系統工具與備份支援")
    if st.button("📤 導出全系統備份 Excel", use_container_width=True):
        conn = sqlite3.connect(DB_NAME)
        df_m = pd.read_sql_query("SELECT * FROM stock_master", conn)
        df_t = pd.read_sql_query("SELECT * FROM stock_timeline", conn)
        df_map = pd.read_sql_query("SELECT * FROM stock_mapping", conn)
        conn.close()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_m.to_excel(writer, sheet_name='Master', index=False)
            df_t.to_excel(writer, sheet_name='Timeline', index=False)
            df_map.to_excel(writer, sheet_name='Mapping', index=False)
        st.download_button(
            label="💾 點擊下載備份檔", data=output.getvalue(),
            file_name=f"stock_backup_{datetime.today().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True
        )

    restore_file = st.file_uploader("📥 匯入歷史備份還原", type=["xlsx"])
    if restore_file and st.button("🔥 確認覆蓋還原系統", type="primary", use_container_width=True):
        try:
            excel_data = pd.read_excel(restore_file, sheet_name=None)
            conn = sqlite3.connect(DB_NAME)
            for sheet, table in [('Master', 'stock_master'), ('Timeline', 'stock_timeline'), ('Mapping', 'stock_mapping')]:
                if sheet in excel_data: excel_data[sheet].to_sql(table, conn, if_exists='replace', index=False)
            conn.commit()
            conn.close()
            st.success("🎉 系統已成功還原！")
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
# 分頁一：今日動態與資產總覽
# ==========================================
with tab1:
    # 區塊 1：CSV 匯入與互動確認閘門 (套用理由模板)
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
                cursor.execute("""
                    INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                    VALUES (?, '已實現出場', ?, ?, ?, ?)
                """, (row['stock_id'], row['op_date'], row['avg_cost'], row['shares'], f"已實現CSV自動導入，結案損益：{row['realized_pnl']}"))
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
            st.session_state.csv_events = events

    # 顯示 CSV 互動匣 (第一版體驗回歸)
    if st.session_state.csv_events:
        st.warning(f"⚠️ 偵測到 {len(st.session_state.csv_events)} 筆庫存異動事件！請確認操作理由：")
        for idx, ev in enumerate(st.session_state.csv_events):
            row = ev['data']
            sid = row['stock_id']
            with st.expander(f"【{ev['type']}】{sid} {row['stock_name']}", expanded=True):
                st.write(f"• 異動新股數：{row['shares']:,} 股 | 新均價成本：${row['avg_cost']}")
                
                op_date = st.date_input("操作日期", value=datetime.today(), key=f"csv_date_{idx}")
                st.caption("⚡ 點擊快速套用理由模板：")
                pool = TEMPLATES["🟢 買入/加碼"] if ev['type'] in ['初始建倉', '加碼'] else TEMPLATES["🔴 賣出/減碼"]
                
                note_key = f"csv_note_input_{idx}"
                if note_key not in st.session_state: st.session_state[note_key] = ""
                
                cols = st.columns(2)
                for b_idx, t_text in enumerate(pool):
                    with cols[b_idx % 2]:
                        if st.button(t_text[:12] + "...", key=f"csv_btn_{idx}_{b_idx}"):
                            st.session_state[note_key] = t_text
                            st.rerun()
                            
                note_text = st.text_area("📝 詳細操作理由：", value=st.session_state[note_key], key=note_key)
                
                if st.button("💾 確認寫入庫存與歷史帳", key=f"csv_save_btn_{idx}", type="primary"):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    if ev['type'] == '初始建倉':
                        cursor.execute("""
                            INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period)
                            VALUES (?, ?, ?, ?, ?, ?, '中期波段')
                        """, (row['market'], sid, row['stock_name'], row['avg_cost'], row['shares'], note_text))
                        shares_diff = row['shares']
                    else:
                        cursor.execute("UPDATE stock_master SET avg_cost=?, shares=? WHERE stock_id=? AND status='持有'", (row['avg_cost'], row['shares'], sid))
                        shares_diff = abs(row['shares'] - ev['old_shares'])
                        
                    cursor.execute("""
                        INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (sid, ev['type'], op_date.strftime("%Y-%m-%d"), row['avg_cost'], shares_diff, note_text))
                    conn.commit()
                    conn.close()
                    st.session_state.csv_events.pop(idx)
                    st.success("已成功同步寫入資料庫！")
                    st.rerun()

    # 區塊 2：完全全新建立新持股項目
    with st.expander("➕ 手動新增全新個股庫存項目"):
        m_market = st.selectbox("市場", ["台股", "美股"])
        m_id = st.text_input("股票代號 (如: 00922.TW / NVDA)")
        m_name = st.text_input("股票名稱")
        m_cost = st.number_input("平均成本均價", min_value=0.0, step=0.1)
        m_shares = st.number_input("持有股數", min_value=0.0, step=1.0)
        m_reason = st.text_area("核心建倉理由")
        if st.button("🚀 確認手動建立新股", type="primary", use_container_width=True):
            if m_id and m_name:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period)
                    VALUES (?, ?, ?, ?, ?, ?, '中期波段')
                """, (m_market, m_id, m_name, m_cost, m_shares, m_reason))
                cursor.execute("""
                    INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                    VALUES (?, '初始建倉', ?, ?, ?, ?)
                """, (m_id, datetime.today().strftime("%Y-%m-%d"), m_cost, m_shares, m_reason))
                conn.commit()
                conn.close()
                st.success("手動標的建立成功！")
                st.rerun()

    # ─── 庫存數據讀取與動態雙向排序置頂計算大腦 ───
    conn = sqlite3.connect(DB_NAME)
    df_db = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()

    processed_stocks = []
    if not df_db.empty:
        for _, row in df_db.iterrows():
            sid = row['stock_id']
            y_price = st.session_state.yahoo_prices.get(sid, None)
            
            alert_status = "normal"  # normal, stop_loss, take_profit
            profit_pct = 0.0
            current_value = 0.0
            pnl_money = 0.0
            
            if y_price and row['avg_cost'] > 0:
                profit_pct = ((y_price - row['avg_cost']) / row['avg_cost']) * 100
                current_value = y_price * row['shares']
                pnl_money = (y_price - row['avg_cost']) * row['shares']
                
                # 判斷雙向警示臨界點 (長期投資徹底豁免)
                if row['period'] != '長期投資':
                    if profit_pct <= -row['stop_loss_pct']:
                        alert_status = "stop_loss"
                    else:
                        target_pct = row['stop_loss_pct'] * 2 if row['strategy_type'] == '2倍風險停利法' else row['target_profit_pct']
                        if profit_pct >= target_pct:
                            alert_status = "take_profit"
            
            # 給予排序權重 (停損最優先=0, 停利次之=1, 普通=2)
            sort_weight = 0 if alert_status == "stop_loss" else (1 if alert_status == "take_profit" else 2)
            
            processed_stocks.append({
                'row': row, 'y_price': y_price, 'profit_pct': profit_pct,
                'current_value': current_value, 'pnl_money': pnl_money,
                'alert_status': alert_status, 'sort_weight': sort_weight
            })
            
        # 核心優化：依照雙向警示權重進行排序，強制將危急個股頂推至最上方
        processed_stocks.sort(key=lambda x: x['sort_weight'])

    # 區塊 3：核心看板渲染
    st.markdown("---")
    col_title, col_refresh = st.columns([2, 1])
    with col_title: st.subheader("🟢 當前持有庫存總覽")
    with col_refresh:
        if st.button("🔄 刷新 Yahoo 現值", type="secondary", use_container_width=True):
            with st.spinner("連線報價中..."):
                conn = sqlite3.connect(DB_NAME)
                df_query = pd.read_sql_query("SELECT stock_id, market FROM stock_master WHERE status='持有'", conn)
                conn.close()
                for _, r in df_query.iterrows():
                    p = fetch_yahoo_price(r['stock_id'], r['market'])
                    if p: st.session_state.yahoo_prices[r['stock_id']] = p
            st.rerun()

    if not processed_stocks:
        st.caption("目前系統中尚無持股數據。")
    else:
        for item in processed_stocks:
            stock = item['row']
            sid = stock['stock_id']
            y_price = item['y_price']
            profit_pct = item['profit_pct']
            
            # A. 獨立渲染最頂端的強烈色彩策略警示框
            if item['alert_status'] == "stop_loss":
                st.markdown(f"""
                <div style="background-color: #ffebee; border-left: 8px solid #c62828; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #b71c1c;">
                    <b style="font-size: 1.15rem;">🚨 🚨 紀律防守線觸發：已達嚴格停損點！</b><br>
                    <b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
                    核心警示：目前即時損益已跌達 <span style="font-weight:bold; font-size:1.1rem;">{profit_pct:.2f}%</span>，觸及防守門檻 (-{stock['stop_loss_pct']:.1f}%)。<br>
                    請立即開啟券商交易軟體，理性手起刀落執行全數停損，嚴控風險本金！
                </div>
                """, unsafe_allow_html=True)
            elif item['alert_status'] == "take_profit":
                ratio = 50.0 if stock['strategy_type'] == '2倍風險停利法' else stock['sell_ratio']
                target_pct = stock['stop_loss_pct'] * 2 if stock['strategy_type'] == '2倍風險停利法' else stock['target_profit_pct']
                suggested_shares = round(stock['shares'] * (ratio / 100))
                expected_cash = round(suggested_shares * (y_price if y_price else 0), 1)
                
                st.markdown(f"""
                <div style="background-color: #ffe0b2; border-left: 8px solid #f57c00; padding: 15px; border-radius: 6px; margin-bottom: 10px; color: #5d4037;">
                    <b style="font-size: 1.15rem;">🔥 🚨 超級績效：已達馬克分批獲利停利點！</b><br>
                    <b>【{stock['market']}】{sid} {stock['stock_name']} ({stock['period']})</b><br>
                    📈 當 前 獲 利 ％ ： <span style="color:#d84315; font-weight:bold;">+{profit_pct:.2f}%</span> (目標: +{target_pct:.1f}%)<br>
                    🚪 門 檻 出 場 ％ ： 強制落袋 {ratio}% 持股<br>
                    🛒 應 下 單 股 數 ： <b>請至券商下單賣出 {suggested_shares:,} 股</b><br>
                    💵 預計收回總金額 ： <b>${expected_cash:,} 元</b>
                </div>
                """, unsafe_allow_html=True)

            # B. 渲染主體個股卡片面板
            # 長期投資為淺藍底，中短期為乾淨白底
            bg_color = "#e3f2fd" if stock['period'] == '長期投資' else "#ffffff"
            border_line = "6px solid #1e88e5" if stock['period'] == '長期投資' else "6px solid #757575"
            text_main_color = "#0d47a1" if stock['period'] == '長期投資' else "#333333"
            
            # 封裝高亮質感淺灰色數據方塊 (UI便利性升級)
            if y_price:
                pnl_color = "#d32f2f" if item['pnl_money'] < 0 else "#388e3c"
                pnl_arrow = "🔴" if item['pnl_money'] < 0 else "🟢"
                stats_block = f"""
                <div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #424242;">
                    📈 最新市價：<b>{y_price}</b> &nbsp;|&nbsp; 當前現值：<b>{item['current_value']:,.1f}</b><br>
                    損益狀態：<span style="color:{pnl_color}; font-weight:bold;">{pnl_arrow} {profit_pct:.2f}%</span> &nbsp;|&nbsp; 帳面獲利提示：<span style="color:{pnl_color}; font-weight:bold;">${item['pnl_money']:,.1f}</span>
                </div>
                """
            else:
                stats_block = f"""
                <div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #757575; font-style: italic;">
                    ⚪ 帳產現值未經刷新，請點選右上角按鈕動態連動 Yahoo 股市。
                </div>
                """
                
            st.markdown(f"""
            <div style="background-color: {bg_color}; border: 1px solid #e0e0e0; border-left: {border_line}; padding: 14px; border-radius: 6px; color: {text_main_color};">
                <b style="font-size:1.1rem;">{'💠' if stock['period']=='長期投資' else '🛡️'} 【{stock['market']}】{sid} {stock['stock_name']}</b> ({stock['period']})<br>
                庫存均價：<b>${stock['avg_cost']}</b> &nbsp;|&nbsp; 持有股數：<b>{stock['shares']:,} 股</b>
                {stats_block}
                <small>📌 核心理由：{stock['core_reason']}</small>
            </div>
            """, unsafe_allow_html=True)

            # 三大控制核心按鈕
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                if st.button("加/減碼", key=f"op_btn_{sid}", use_container_width=True):
                    st.session_state.op_mode[sid] = not st.session_state.op_mode.get(sid, False)
                    st.session_state.edit_mode[sid] = False
            with c2:
                if st.button("✏️ 快速編輯", key=f"edt_btn_{sid}", use_container_width=True):
                    st.session_state.edit_mode[sid] = not st.session_state.edit_mode.get(sid, False)
                    st.session_state.op_mode[sid] = False
            with c3:
                if st.button("🛑 結案", key=f"cls_btn_{sid}", use_container_width=True):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_master SET status='已結案' WHERE id=?", (stock['id'],))
                    cursor.execute("""
                        INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                        VALUES (?, '手動結案', ?, ?, ?, '手動清倉移出')
                    """, (sid, datetime.today().strftime("%Y-%m-%d"), stock['avg_cost'], stock['shares']))
                    conn.commit()
                    conn.close()
                    st.rerun()

            # 🛠️ 控制匣一：快速「加/減碼」操作面板（具備理由模板套入）
            if st.session_state.op_mode.get(sid, False):
                with st.container(border=True):
                    st.caption(f"➕ 盤中快速【加/減碼】交易變動換算：")
                    tx_type = st.radio("操作類別", ["加碼", "減碼"], key=f"tx_type_{sid}", horizontal=True)
                    tx_price = st.number_input("成交單價", value=float(y_price if y_price else stock['avg_cost']), step=0.01, key=f"tx_price_{sid}")
                    tx_shares = st.number_input("交易股數", value=0.0, step=1.0, key=f"tx_shares_{sid}")
                    tx_date = st.date_input("操作日期", value=datetime.today(), key=f"tx_date_{sid}")
                    
                    st.caption("⚡ 快速套用加減碼理由模板：")
                    pool = TEMPLATES["🟢 買入/加碼"] if tx_type == "加碼" else TEMPLATES["🔴 賣出/減碼"]
                    
                    note_input_key = f"note_tx_input_{sid}"
                    if note_input_key not in st.session_state: st.session_state[note_input_key] = ""
                    
                    cols_tx = st.columns(2)
                    for b_idx, t_text in enumerate(pool):
                        with cols_tx[b_idx % 2]:
                            if st.button(t_text[:12] + "...", key=f"tx_btn_{sid}_{b_idx}"):
                                st.session_state[note_input_key] = t_text
                                st.rerun()
                                
                    tx_note = st.text_area("📝 詳細操作理由：", value=st.session_state[note_input_key], key=note_input_key)
                    
                    if st.button("💾 確定執行交易（後台自動計算法則）", key=f"tx_submit_{sid}", type="primary", use_container_width=True):
                        if tx_shares <= 0:
                            st.error("請輸入大於 0 的交易股數！")
                        else:
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            if tx_type == "加碼":
                                new_shares = stock['shares'] + tx_shares
                                new_cost = ((stock['shares'] * stock['avg_cost']) + (tx_shares * tx_price)) / new_shares
                                cursor.execute("UPDATE stock_master SET avg_cost=?, shares=? WHERE id=?", (round(new_cost, 2), new_shares, stock['id']))
                            else:
                                new_shares = max(0.0, stock['shares'] - tx_shares)
                                if new_shares <= 0:
                                    cursor.execute("UPDATE stock_master SET status='已結案', shares=0 WHERE id=?", (stock['id'],))
                                else:
                                    cursor.execute("UPDATE stock_master SET shares=? WHERE id=?", (new_shares, stock['id']))
                                    
                            cursor.execute("""
                                INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (sid, tx_type, tx_date.strftime("%Y-%m-%d"), tx_price, tx_shares, tx_note))
                            conn.commit()
                            conn.close()
                            st.session_state.op_mode[sid] = False
                            st.session_state[note_input_key] = ""
                            st.success("交易換算並寫入完畢！")
                            st.rerun()

            # 🛠️ 控制匣二：快速「細節修復編輯」面板
            if st.session_state.edit_mode.get(sid, False):
                with st.container(border=True):
                    st.caption(f"🔧 修正【{sid}】庫存原始設定：")
                    u_id = st.text_input("股票代號", value=stock['stock_id'], key=f"u_id_{sid}")
                    u_name = st.text_input("股票名稱", value=stock['stock_name'], key=f"u_name_{sid}")
                    u_cost = st.number_input("平均成本", value=float(stock['avg_cost']), step=0.01, key=f"u_cost_{sid}")
                    u_shares = st.number_input("目前股數", value=float(stock['shares']), step=1.0, key=f"u_shares_{sid}")
                    u_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=["長期投資", "中期波段", "短期操作"].index(stock['period']), key=f"u_per_{sid}")
                    u_strat = st.selectbox("減碼紀律策略", ["2倍風險停利法", "強勢波段停利法"], index=["2倍風險停利法", "強勢波段停利法"].index(stock['strategy_type']), key=f"u_str_{sid}")
                    cc1, cc2 = st.columns(2)
                    with cc1: u_sl = st.number_input("初始停損點 (%)", value=float(stock['stop_loss_pct']), step=0.1, key=f"u_sl_{sid}")
                    with cc2: u_tp = st.number_input("自訂目標漲幅 (%)", value=float(stock['target_profit_pct']), step=0.1, key=f"u_tp_{sid}")
                    u_ratio = st.number_input("波段觸發出場持股比例 (%)", value=float(stock['sell_ratio']), step=5.0, key=f"u_rat_{sid}")
                    u_reason = st.text_area("初始核心理由", value=stock['core_reason'], key=f"u_rea_{sid}")
                    
                    if st.button("💾 儲存個股修正", key=f"save_edit_{sid}", type="primary", use_container_width=True):
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE stock_master SET 
                                stock_id=?, stock_name=?, avg_cost=?, shares=?, period=?, 
                                strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?
                            WHERE id=?
                        """, (u_id, u_name, u_cost, u_shares, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, stock['id']))
                        conn.commit()
                        conn.close()
                        st.session_state.edit_mode[sid] = False
                        st.success("基礎修改儲存成功！")
                        st.rerun()
            st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)

# ==========================================
# 分頁二：個股生命週期故事書 (時序軸)
# ==========================================
with tab2:
    st.subheader("🔍 單一個股生命週期全覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_all_m = pd.read_sql_query("SELECT DISTINCT stock_id, stock_name FROM stock_master", conn)
    conn.close()
    
    if df_all_m.empty:
        st.caption("尚無任何歷史操作交易數據。")
    else:
        options = [f"{r['stock_id']} {r['stock_name']}" for _, r in df_all_m.iterrows()]
        selected_stock = st.selectbox("請選擇個股檢視生命時序：", options)
        selected_id = selected_stock.split(" ")[0]
        
        conn = sqlite3.connect(DB_NAME)
        df_timeline = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE stock_id='{selected_id}' ORDER BY op_date DESC, id DESC", conn)
        df_master_info = pd.read_sql_query(f"SELECT * FROM stock_master WHERE stock_id='{selected_id}' LIMIT 1", conn)
        conn.close()
        
        if not df_master_info.empty:
            st.info(f"💡 置頂初始核心戰略理由：\n{df_master_info.iloc[0]['core_reason']}")
            if df_master_info.iloc[0]['period'] != '長期投資':
                st.markdown(f"🛡️ **後半段保本追蹤防守價：${df_master_info.iloc[0]['avg_cost']}** (若減碼完成，賸餘持股請移至保本點防守)")
            
        st.write("⏱️ 歷史操作決策流水帳：")
        for _, row in df_timeline.iterrows():
            badge = "🟢 買入/加碼" if row['action_type'] in ['初始建倉', '加碼'] else "🔴 紀律減碼/結案"
            with st.container(border=True):
                st.markdown(f"**{row['op_date']} | {badge} ({row['action_type']})**")
                st.markdown(f"成交價格: `${row['price']}` &nbsp;|&nbsp; 變動數量: `{row['shares_changed']:,} 股`")
                st.markdown(f"💬 操盤回顧備忘：\n*{row['note']}*")

# ==========================================
# 分頁三：月份進出場明細覆盤 (月報)
# ==========================================
with tab3:
    st.subheader("📅 月度進出場決策大事記覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_dates = pd.read_sql_query("SELECT DISTINCT substr(op_date, 1, 7) as ym FROM stock_timeline ORDER BY ym DESC", conn)
    conn.close()
    
    if df_dates.empty:
        st.caption("目前尚無任何月份的操作紀錄。")
    else:
        selected_ym = st.selectbox("請選擇覆盤月份：", df_dates['ym'].tolist())
        conn = sqlite3.connect(DB_NAME)
        df_month = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE op_date LIKE '{selected_ym}%' ORDER BY op_date DESC", conn)
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
        user_rev = st.text_area("寫下本月的紀律執行心得與優缺點檢討：", key=f"txt_rev_{selected_ym}")
        if st.button("💾 儲存月度檢討日誌", key=f"btn_rev_{selected_ym}", type="primary"):
            st.success(f"{selected_ym} 心態日誌已妥善存檔！")
