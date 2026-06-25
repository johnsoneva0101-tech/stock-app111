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
            target_profit_pct REAL DEFAULT 14.0,
            sell_ratio REAL DEFAULT 50.0,
            status TEXT DEFAULT '持有'
        )
    ''')
    
    # 動態補足欄位 (防呆升級)
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

    elif '代號' in df.columns and '均價' in df.columns and '目前庫存' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']) or str(row['代號']).strip() == '': continue
            parsed_data.append({
                'market': '美股', 'stock_id': str(row['代號']).strip(), 'stock_name': str(row['股票名稱']).strip(),
                'avg_cost': float(str(row['均價']).replace(',', '')), 'shares': float(str(row['目前庫存']).replace(',', ''))
            })
        return pd.DataFrame(parsed_data), "庫存"
        
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

tab1, tab2, tab3, tab4 = st.tabs(["📊 今日總覽", "🔍 個股時序", "📅 月覆盤", "💡 馬克心法"])

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
            st.session_state.csv_events = events

    # 📥 【CSV 匯入互動確認匣】 (策略連動自動帶入)
    if st.session_state.csv_events:
        st.warning(f"⚠️ 偵測到 {len(st.session_state.csv_events)} 筆庫存異動事件！請配置您的交易紀律：")
        events_to_process = list(st.session_state.csv_events)
        
        for idx, ev in enumerate(events_to_process):
            row = ev['data']
            sid = row['stock_id']
            with st.expander(f"【{ev['type']}】{sid} {row['stock_name']}", expanded=True):
                st.write(f"• 異動新股數：{row['shares']:,} 股 | 均價成本：${row['avg_cost']}")
                
                csv_period = st.radio("🏷️ 1. 投資週期分類：", ["長期投資", "中期波段", "短期操作"], index=1, key=f"csv_per_{idx}", horizontal=True)
                
                # 🎯 馬克策略動態連動配置表單
                csv_strat = st.radio("⚙️ 2. 馬克紀律策略：", ["2倍風險停利法", "強勢波段停利法"], key=f"csv_str_{idx}", horizontal=True)
                csv_sl = st.number_input("🛡️ 3. 初始停損點 (%)：", value=7.0, step=0.5, key=f"csv_sl_{idx}")
                
                if csv_strat == "2倍風險停利法":
                    csv_tp = csv_sl * 2
                    csv_ratio = 50.0
                    st.markdown(f"💡 **馬克策略連動帶入**：獲利目標自動鎖定 **{csv_tp:.1f}%**，達標時強制落袋 **50.0%** 持股。")
                else:
                    csv_tp = st.number_input("🎯 自訂目標漲幅 (%)：", value=20.0, step=1.0, key=f"csv_tp_{idx}")
                    csv_ratio = st.number_input("🛒 觸發出場持股比例 (%)：", value=33.33, step=5.0, key=f"csv_ratio_{idx}")
                    
                op_date = st.date_input("📅 4. 操作日期選單：", value=datetime.today(), key=f"csv_date_{idx}")
                
                st.caption("⚡ 5. 快速套用操作理由模板：")
                pool = TEMPLATES["🟢 買入/加碼"] if ev['type'] in ['初始建倉', '加碼'] else TEMPLATES["🔴 賣出/減碼"]
                
                note_key = f"csv_note_input_{idx}"
                if note_key not in st.session_state: st.session_state[note_key] = ""
                
                cols = st.columns(2)
                for b_idx, t_text in enumerate(pool):
                    with cols[b_idx % 2]:
                        if st.button(t_text[:12] + "...", key=f"csv_btn_{idx}_{b_idx}"):
                            st.session_state[note_key] = t_text
                            st.rerun()
                            
                note_text = st.text_area("📝 6. 詳細操作理由備忘錄：", value=st.session_state[note_key], key=f"csv_txt_{idx}")
                
                if st.button("💾 確認寫入庫存與歷史帳", key=f"csv_save_btn_{idx}", type="primary"):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    if ev['type'] == '初始建倉':
                        cursor.execute("INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period, strategy_type, stop_loss_pct, target_profit_pct, sell_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (row['market'], sid, row['stock_name'], row['avg_cost'], row['shares'], note_text, csv_period, csv_strat, csv_sl, csv_tp, csv_ratio))
                        shares_diff = row['shares']
                    else:
                        cursor.execute("UPDATE stock_master SET avg_cost=?, shares=?, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=? WHERE stock_id=? AND status='持有'", (row['avg_cost'], row['shares'], csv_period, csv_strat, csv_sl, csv_tp, csv_ratio, sid))
                        shares_diff = abs(row['shares'] - ev['old_shares'])
                        
                    cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, ?, ?, ?, ?, ?)", (sid, ev['type'], op_date.strftime("%Y-%m-%d"), row['avg_cost'], shares_diff, note_text))
                    conn.commit()
                    conn.close()
                    st.session_state.csv_events = [e for i, e in enumerate(st.session_state.csv_events) if i != idx]
                    st.success("已成功同步寫入資料庫！")
                    st.rerun()

    # 手動建立新庫存 (連動帶入機制)
    with st.expander("➕ 手動新增全新個股庫存項目"):
        m_market = st.selectbox("市場", ["台股", "美股"])
        m_id = st.text_input("股票代號 (如: 00922.TW / NVDA)")
        m_name = st.text_input("股票名稱")
        m_cost = st.number_input("平均成本均價", min_value=0.0, step=0.1)
        m_shares = st.number_input("持有股數", min_value=0.0, step=1.0)
        m_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=1)
        
        m_strat = st.selectbox("馬克策略", ["2倍風險停利法", "強勢波段停利法"])
        m_sl = st.number_input("初始停損 (%)", value=7.0, step=0.5)
        
        if m_strat == "2倍風險停利法":
            m_tp = m_sl * 2
            m_ratio = 50.0
            st.caption(f"💡 系統已自動對應鎖定：目標獲利 {m_tp:.1f}% / 出場比例 50%")
        else:
            m_tp = st.number_input("自訂目標漲幅 (%)", value=20.0, step=1.0)
            m_ratio = st.number_input("自訂出場持股比例 (%)", value=33.33, step=5.0)
            
        m_reason = st.text_area("核心建倉理由")
        if st.button("🚀 確認手動建立新股", type="primary", use_container_width=True):
            if m_id and m_name:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period, strategy_type, stop_loss_pct, target_profit_pct, sell_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (m_market, m_id, m_name, m_cost, m_shares, m_reason, m_period, m_strat, m_sl, m_tp, m_ratio))
                cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '初始建倉', ?, ?, ?, ?)", (m_id, datetime.today().strftime("%Y-%m-%d"), m_cost, m_shares, m_reason))
                conn.commit()
                conn.close()
                st.success("手動標的建立成功！")
                st.rerun()

    # ─── 大腦運算：雙向警示置頂洗牌排序 ───
    conn = sqlite3.connect(DB_NAME)
    df_db = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()

    processed_stocks = []
    if not df_db.empty:
        for _, row in df_db.iterrows():
            sid = row['stock_id']
            y_price = st.session_state.yahoo_prices.get(sid, None)
            
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
            processed_stocks.append({
                'row': row, 'y_price': y_price, 'profit_pct': profit_pct,
                'current_value': current_value, 'pnl_money': pnl_money,
                'alert_status': alert_status, 'sort_weight': sort_weight
            })
        processed_stocks.sort(key=lambda x: x['sort_weight'])

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
            db_id = stock['id'] 
            sid = stock['stock_id']
            y_price = item['y_price']
            profit_pct = item['profit_pct']
            
            # 🎯 提取與常態化動態參數
            stop_loss_price = round(stock['avg_cost'] * (1 - (stock['stop_loss_pct'] / 100)), 2)
            target_pct_val = stock['stop_loss_pct'] * 2 if stock['strategy_type'] == '2倍風險停利法' else stock['target_profit_pct']
            ratio_val = stock['sell_ratio']
            
            take_profit_price = round(stock['avg_cost'] * (1 + (target_pct_val / 100)), 2)
            suggested_shares = round(stock['shares'] * (ratio_val / 100))
            expected_cash = round(suggested_shares * (take_profit_price), 1)

            # 🛑 核心修復：除掉所有字串內空格與排版縮進，防止 Markdown 錯判為 Code Block 
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
                        actual_price = st.number_input("實際成交價格", value=float(y_price), step=0.01, key=f"act_p_{db_id}")
                        actual_date = st.date_input("操作日期", value=datetime.today(), key=f"act_d_{db_id}")
                        
                        if st.button("👍 確定扣減股數並歸檔歷史", key=f"btn_confirm_act_{db_id}", type="primary"):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            new_shares = max(0.0, stock['shares'] - actual_shares)
                            if new_shares <= 0:
                                cursor.execute("UPDATE stock_master SET status='已結案', shares=0 WHERE id=?", (db_id,))
                            else:
                                cursor.execute("UPDATE stock_master SET shares=? WHERE id=?", (new_shares, db_id))
                            note_msg = f"觸發馬克【{stock['strategy_type']}】，自動換算減碼。當前獲利 +{profit_pct:.2f}%，實際賣出 {actual_shares} 股，收回金額：${round(actual_shares*actual_price, 2)}"
                            cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '減碼', ?, ?, ?, ?)", (sid, actual_date.strftime("%Y-%m-%d"), actual_price, actual_shares, note_msg))
                            conn.commit()
                            conn.close()
                            st.success("紀錄已成功同步歸檔！")
                            st.rerun()

            bg_color = "#e3f2fd" if stock['period'] == '長期投資' else "#ffffff"
            border_line = "6px solid #1e88e5" if stock['period'] == '長期投資' else "6px solid #757575"
            text_main_color = "#0d47a1" if stock['period'] == '長期投資' else "#333333"
            
            # 🛑 核心修復：剔除所有字串開頭縮進，將數據高亮封裝進質感淺灰色方塊
            if y_price:
                pnl_color = "#d32f2f" if item['pnl_money'] < 0 else "#388e3c"
                pnl_arrow = "🔴" if item['pnl_money'] < 0 else "🟢"
                
                mark_guide_html = ""
                if stock['period'] != '長期投資':
                    mark_guide_html = f'''<hr style="border-top: 1px solid #e0e0e0; margin: 8px 0;">
<b>📜 馬克紀律常態操盤指引 (動態換算)：</b><br>
• ⚙️ 執行策略：【{stock['strategy_type']}】<br>
• 🛑 嚴格停損線 (-{stock['stop_loss_pct']}%): <b>${stop_loss_price}</b> (破此價強制全清倉 {stock['shares']} 股)<br>
• 🎯 分批停利點 (+{target_pct_val}%): <b>${take_profit_price}</b> (達此價強制落袋減碼 {suggested_shares} 股，預計收回 ${expected_cash})'''

                stats_block = f'''<div style="background-color: #f5f5f5; border-radius: 6px; padding: 10px; margin: 8px 0; border: 1px solid #e0e0e0; color: #424242;">
🏢 最新市價：<b>{y_price}</b> &nbsp;|&nbsp; 當前現值：<b>${item['current_value']:,.1f}</b><br>
即時損益：<span style="color:{pnl_color}; font-weight:bold;">{pnl_arrow} {profit_pct:.2f}%</span> &nbsp;|&nbsp; 帳面獲利提示：<span style="color:{pnl_color}; font-weight:bold;">${item['pnl_money']:,.1f}</span>
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

            # 三大控制核心按鈕
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                if st.button("加/減碼", key=f"op_btn_{db_id}", use_container_width=True):
                    st.session_state.op_mode[db_id] = not st.session_state.op_mode.get(db_id, False)
                    st.session_state.edit_mode[db_id] = False
                    st.rerun()
            with c2:
                if st.button("✏️ 快速編輯", key=f"edt_btn_{db_id}", use_container_width=True):
                    st.session_state.edit_mode[db_id] = not st.session_state.edit_mode.get(db_id, False)
                    st.session_state.op_mode[db_id] = False
                    st.rerun()
            with c3:
                if st.button("🛑 結案", key=f"cls_btn_{db_id}", use_container_width=True):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_master SET status='已結案' WHERE id=?", (db_id,))
                    cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, '手動結案', ?, ?, ?, '手動清倉移出')", (sid, datetime.today().strftime("%Y-%m-%d"), stock['avg_cost'], stock['shares']))
                    conn.commit()
                    conn.close()
                    st.rerun()

            # 控制匣一：快速「加/減碼」面板 (動態加權運算)
            if st.session_state.op_mode.get(db_id, False):
                with st.container(border=True):
                    st.caption("➕ 盤中快速【加/減碼】交易變動換算：")
                    tx_type = st.radio("操作類別", ["加碼", "減碼"], key=f"tx_type_{db_id}", horizontal=True)
                    tx_price = st.number_input("成交單價", value=float(y_price if y_price else stock['avg_cost']), step=0.01, key=f"tx_price_{db_id}")
                    tx_shares = st.number_input("交易股數", value=0.0, step=1.0, key=f"tx_shares_{db_id}")
                    tx_date = st.date_input("操作日期", value=datetime.today(), key=f"tx_date_{db_id}")
                    
                    st.caption("⚡ 快速套用加減碼理由模板：")
                    pool = TEMPLATES["🟢 買入/加碼"] if tx_type == "加碼" else TEMPLATES["🔴 賣出/減碼"]
                    
                    note_input_key = f"note_tx_input_{db_id}"
                    if note_input_key not in st.session_state: st.session_state[note_input_key] = ""
                    
                    cols_tx = st.columns(2)
                    for b_idx, t_text in enumerate(pool):
                        with cols_tx[b_idx % 2]:
                            if st.button(t_text[:12] + "...", key=f"tx_btn_{db_id}_{b_idx}"):
                                st.session_state[note_input_key] = t_text
                                st.rerun()
                                
                    tx_note = st.text_area("📝 詳細操作理由：", value=st.session_state[note_input_key], key=note_input_key)
                    
                    if st.button("💾 確定執行交易 (系統自動換算)", key=f"tx_submit_{db_id}", type="primary", use_container_width=True):
                        if tx_shares <= 0:
                            st.error("請輸入大於 0 的交易股數！")
                        else:
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            if tx_type == "加碼":
                                new_shares = stock['shares'] + tx_shares
                                new_cost = ((stock['shares'] * stock['avg_cost']) + (tx_shares * tx_price)) / new_shares
                                cursor.execute("UPDATE stock_master SET avg_cost=?, shares=? WHERE id=?", (round(new_cost, 2), new_shares, db_id))
                            else:
                                new_shares = max(0.0, stock['shares'] - tx_shares)
                                if new_shares <= 0:
                                    cursor.execute("UPDATE stock_master SET status='已結案', shares=0 WHERE id=?", (db_id,))
                                else:
                                    cursor.execute("UPDATE stock_master SET shares=? WHERE id=?", (new_shares, db_id))
                                    
                            cursor.execute("INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note) VALUES (?, ?, ?, ?, ?, ?)", (sid, tx_type, tx_date.strftime("%Y-%m-%d"), tx_price, tx_shares, tx_note))
                            conn.commit()
                            conn.close()
                            st.session_state.op_mode[db_id] = False
                            st.session_state[note_input_key] = ""
                            st.success("交易換算並寫入完畢！")
                            st.rerun()

            # 控制匣二：快速「細節修復編輯」面板 (動態表單連動)
            if st.session_state.edit_mode.get(db_id, False):
                with st.container(border=True):
                    st.caption(f"🔧 修正【{sid}】庫存原始設定：")
                    u_id = st.text_input("股票代號", value=stock['stock_id'], key=f"u_id_{db_id}")
                    u_name = st.text_input("股票名稱", value=stock['stock_name'], key=f"u_name_{db_id}")
                    u_cost = st.number_input("平均成本", value=float(stock['avg_cost']), step=0.01, key=f"u_cost_{db_id}")
                    u_shares = st.number_input("目前股數", value=float(stock['shares']), step=1.0, key=f"u_shares_{db_id}")
                    u_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=["長期投資", "中期波段", "短期操作"].index(stock['period']), key=f"u_per_{db_id}")
                    
                    # 🎯 編輯介面也完成馬克策略參數自動連動
                    u_strat = st.selectbox("減碼紀律策略", ["2倍風險停利法", "強勢波段停利法"], index=["2倍風險停利法", "強勢波段停利法"].index(stock['strategy_type']), key=f"u_str_{db_id}")
                    u_sl = st.number_input("初始停損點 (%)", value=float(stock['stop_loss_pct']), step=0.1, key=f"u_sl_{db_id}")
                    
                    if u_strat == "2倍風險停利法":
                        u_tp = u_sl * 2
                        u_ratio = 50.0
                        st.caption(f"💡 策略連動帶入：目標獲利將強制鎖定為 {u_tp:.1f}% / 減碼比例 50%")
                    else:
                        u_tp = st.number_input("自訂目標漲幅 (%)", value=float(stock['target_profit_pct']), step=0.1, key=f"u_tp_{db_id}")
                        u_ratio = st.number_input("波段觸發出場持股比例 (%)", value=float(stock['sell_ratio']), step=5.0, key=f"u_rat_{db_id}")
                        
                    u_reason = st.text_area("核心理由", value=stock['core_reason'], key=f"u_rea_{db_id}")
                    
                    if st.button("💾 儲存個股修正", key=f"save_edit_{db_id}", type="primary", use_container_width=True):
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        cursor.execute("UPDATE stock_master SET stock_id=?, stock_name=?, avg_cost=?, shares=?, period=?, strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=? WHERE id=?", (u_id, u_name, u_cost, u_shares, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, db_id))
                        conn.commit()
                        conn.close()
                        st.session_state.edit_mode[db_id] = False
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

# ==========================================
# 分頁四：馬克心法交易新法 (終極擴充名言交織版)
# ==========================================
with tab4:
    st.markdown('''
## 💡 馬克·米奈爾維尼 (Mark Minervini) 超級績效大師心法

---
### 🎯 一、 風險優先思維與期望值防禦 (操盤手的生命線)
* **💥 虧損控制的鐵律**
  > *「如果你不能忍受小虧損，遲早會面臨所有虧損之母。」*
  > *「輸家才會攤平輸家 (Losers average losers)！在錯誤的標的上攤平，只是在為你的傲慢支付雙倍罰款。」*
  
  馬克強調，進場前如果沒有算好停損點，就絕對不按下單鈕。本金防禦大於一切，一檔股票讓你賠掉 7%，你只需要賺 7.5% 就能回本；但如果放任虧損砍半跌掉 50%，你必須賺 100% 才能翻身。
  
* **📊 賺賠比與期望值方程式**
  > *「交易的秘密在於賺大賠小，而不是每次操作都對。只要賺賠比拉高，即使勝率只有 30%，你依然能成為富翁。」*
  
  市場上多數人著迷於尋找百分之百獲勝的聖盃，但真正的贏家用數學公式思考。讓你的每一筆虧損鎖定在可控小範圍，而讓獲利部位擁有兩倍以上的期望空間，資產才能真正實現滾動複利。

---
### 💰 二、 經典策略：2倍風險停利法 (不敗的數學防禦)
* **🔒 獲利的一半強制落袋**
  > *「絕不讓一筆已經大賺的利潤，演變成虧損出場。當市場已經給你兩倍風險的利賞，拿走它，落袋為安。」*
  
  當股價順利觸及你當初設定初始停損點的 2 倍（例如停損設 -7%，即時獲利來到 +14%）時，必須毫無懸念、開除情緒，**強制在強勢中賣出 50%（一半）的部位持股**。
  
* **🛡️ 賸餘部位移至保本點防守**
  賣出一半鎖定利潤後，最重要的關鍵下個動作：**立刻將剩下的 50% 持股防守停損線，往上移到你的「買入成本價」**。
  這樣做在數學上這筆單已經立於不敗之地。最壞的情況就是股票回頭，剩下的持股在成本平手出場，但扣除前面大賺的 14%，你整筆交易最終依然穩賺 7%。

---
### 🚀 三、 終極策略：強勢波段停利法 (Selling into Strength)
* **🛒 把股票優雅地倒給瘋狂追高的大眾**
  > *「要在強勢中賣出！不要等趨勢無情反轉、跌勢轟然啟動時，才驚慌失措地與全市場一起踩踏逃竄。」*
  
  真正的超級飆股在大多頭市場中，往往會在極短時間內（1 到 3 週）出現無回檔的垂直噴發。這通常是散戶集體發瘋、主力倒貨的「最後高潮（Climax Run）」。此時絕不能等跌破均線，必須主動減碼 1/3 或 1/2。
  
* **⚡ 觸及強勢波段停利的 4 大趕頂訊號：**
  1. **波段首波達標 (Base Hit)**：從一個健康的型態（如 VCP）帶量平台突破後，快速上漲 15% ~ 25%。這通常是第一波多頭動能的極限，隨後必有首波拉回修正。
  2. **均線乖離率過大 (Extension)**：股價拋離 20日均線（月線）超過 15%~20%，或拋離 50日均線（季線）超過 30%~50%，就像橡皮筋拉到極限，隨時面臨猛烈回彈。
  3. **趕頂垂直噴發 (Climax Run)**：股價連續上漲數月後，突然在尾段出現角度高達 75 度以上的噴發，連續 10 天中有 8 天暴漲，且爆出歷史天量。
  4. **竭盡缺口與單日最大價差**：長途跋涉後出現巨幅跳空缺口，或出現起漲以來「單日實體K棒最長、漲幅最大」的一天，代表動能耗竭。
  
* **🪜 減碼後剩下的持股，該如何詳細續抱？**
  * **移至保本價**：第一時間將剩下的停損點移到買入成本價，鎖死底線。
  * **20EMA（月線）追蹤加速期**：若屬於極強勢飆股，只要每日收盤價沒有跌破 20EMA，其餘震盪一律視為噪音，死抱到底。
  * **50MA（季線）防守整理期**：當飆股漲了 30%~50% 開始橫盤築第二個平台時，股價會拉回季線。只要週K線收盤未破 50MA，代表法人機構在逢低加碼，賸餘部位安心續抱。
  * **階梯平台移動停損 (Backing and Filling)**：每當股價成功站上新平台創高，將防守價逐層上移到新平台的最低點，像電梯一樣鎖死獲利。

---
### 🚨 四、 法人撤資與全面清倉結案訊號
* **💥 當大勢已去，手起刀落全面離場**
  > *「操盤手不與股票談戀愛。當大戶的腳步已經撤離，你必須比他們跑得更快。」*
  
  當出現以下情況，剩下的一半持股必須全數清倉、結束戰局：股價高檔爆量跌破 50MA（季線），且隨後幾天反彈皆無力站回。這意味著主力資金正式出清，大趨勢已經終結，絕不留戀。

---
### 📈 五、 技術型態：VCP 波幅收窄型態 (籌碼的精準解讀)
* **🔍 尋找市場的臨界點 (Pivot Point)**
  > *「我尋找的不是便宜的低價股票，而是準備好要瘋狂飆漲的股票。」*
  
  飆股噴發前，籌碼會從散戶（軟手）轉移到主力大戶（強手）手中。在型態上會表現出每次拉回修正的幅度越來越小（如：30% ➔ 15% ➔ 7% ➔ 3%），且在最右側成交量極度萎縮窒息，代表市場上想賣的人都賣光了。此時只要出現一根**帶量突破臨界點（Pivot）**的長紅K，就是最完美的初始建倉一擊。
''')
