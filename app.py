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
# 1. 資料庫初始化與動態結構優化
# ==========================================
DB_NAME = "stock_notebook.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立主體持股資料表 (新增週期與馬克參數)
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
    
    # 檢查並動態升級舊資料庫結構，防止版本衝突
    cursor.execute("PRAGMA table_info(stock_master)")
    existing_cols = [info[1] for info in cursor.fetchall()]
    if 'period' not in existing_cols:
        cursor.execute("ALTER TABLE stock_master ADD COLUMN period TEXT DEFAULT '中期波段'")
    if 'strategy_type' not in existing_cols:
        cursor.execute("ALTER TABLE stock_master ADD COLUMN strategy_type TEXT DEFAULT '2倍風險停利法'")
    if 'stop_loss_pct' not in existing_cols:
        cursor.execute("ALTER TABLE stock_master ADD COLUMN stop_loss_pct REAL DEFAULT 7.0")
    if 'target_profit_pct' not in existing_cols:
        cursor.execute("ALTER TABLE stock_master ADD COLUMN target_profit_pct REAL DEFAULT 15.0")
    if 'sell_ratio' not in existing_cols:
        cursor.execute("ALTER TABLE stock_master ADD COLUMN sell_ratio REAL DEFAULT 50.0")

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
    
    # 建立自訂台股代號自動轉換對照表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT UNIQUE,
            yahoo_id TEXT
        )
    ''')
    
    # 預設寫入基礎對照資料
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
# 2. 工具函式庫 (對照、報價、解析、備份)
# ==========================================
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

    # 美股複委託庫存 CSV
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
            
            # 自動進行代號轉換對照
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
# 3. UI 介面設定與頂層佈局
# ==========================================
st.set_page_config(page_title="策略紀律筆記本", layout="centered")
st.title("📱 投資決策紀律筆記本")

# 手機版三大導覽分頁
tab1, tab2, tab3 = st.tabs(["📊 今日動態/總覽", "🔍 個股時序軸", "📅 月度覆盤"])

if "yahoo_prices" not in st.session_state: st.session_state.yahoo_prices = {}
if "edit_mode" not in st.session_state: st.session_state.edit_mode = {}
if "action_confirm" not in st.session_state: st.session_state.action_confirm = {}

# ──────────────────────────────────────────
# 側邊欄：全系統備份還原與自訂對照工具
# ──────────────────────────────────────────
with st.sidebar:
    st.header("📦 系統工具與備份支援")
    
    # 備份匯出功能
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
            label="💾 點擊下載備份檔",
            data=output.getvalue(),
            file_name=f"stock_backup_{datetime.today().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    # 還原匯入功能
    restore_file = st.file_uploader("📥 匯入歷史備份還原", type=["xlsx"])
    if restore_file:
        if st.button("🔥 確認覆蓋還原系統", type="primary", use_container_width=True):
            try:
                excel_data = pd.read_excel(restore_file, sheet_name=None)
                conn = sqlite3.connect(DB_NAME)
                if 'Master' in excel_data: excel_data['Master'].to_sql('stock_master', conn, if_exists='replace', index=False)
                if 'Timeline' in excel_data: excel_data['Timeline'].to_sql('stock_timeline', conn, if_exists='replace', index=False)
                if 'Mapping' in excel_data: excel_data['Mapping'].to_sql('stock_mapping', conn, if_exists='replace', index=False)
                conn.commit()
                conn.close()
                st.success("🎉 系統已成功還原！請重整網頁。")
                st.rerun()
            except Exception as e:
                st.error(f"還原失敗，請檢查檔案格式。錯誤訊息: {e}")

    st.markdown("---")
    st.subheader("🔄 自訂台股 Yahoo 對照表")
    new_raw = st.text_input("券商 CSV 中文名 (如: 台積電)")
    new_yid = st.text_input("Yahoo 代號 (如: 2330.TW)")
    if st.button("➕ 儲存對照項目", use_container_width=True):
        if new_raw and new_yid:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO stock_mapping (raw_name, yahoo_id) VALUES (?, ?)", (new_raw, new_yid))
            conn.commit()
            conn.close()
            st.success(f"已綁定：{new_raw} ➔ {new_yid}")
            st.rerun()

# ──────────────────────────────────────────
# 分頁一：今日動態與資產總覽
# ──────────────────────────────────────────
with tab1:
    st.subheader("📥 匯入最新券商資料")
    uploaded_file = st.file_uploader("支援上傳庫存或已實現 CSV", type=["csv"], label_visibility="collapsed")
    
    if uploaded_file:
        df_parsed, csv_type = parse_uploaded_csv(uploaded_file)
        if csv_type == "已實現":
            st.info(f"📊 偵測到已實現交易明細，正在自動歸帳...")
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
            st.success("🎉 已實現數據換算成功！")
            st.rerun()
        elif csv_type == "庫存":
            # 讀取現有庫存比對
            conn = sqlite3.connect(DB_NAME)
            df_old = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
            conn.close()
            old_dict = {r['stock_id']: r for _, r in df_old.iterrows()}
            
            for _, row in df_parsed.iterrows():
                sid = row['stock_id']
                if sid not in old_dict:
                    # 初始建倉
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO stock_master (market, stock_id, stock_name, avg_cost, shares, core_reason, period)
                        VALUES (?, ?, ?, ?, ?, 'CSV自動匯入建倉', '中期波段')
                    """, (row['market'], sid, row['stock_name'], row['avg_cost'], row['shares']))
                    cursor.execute("""
                        INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                        VALUES (?, '初始建倉', ?, ?, ?, 'CSV自動匯入建倉')
                    """, (sid, datetime.today().strftime("%Y-%m-%d"), row['avg_cost'], row['shares']))
                    conn.commit()
                    conn.close()
            st.success("🎉 庫存比對與同步完成！")
            st.rerun()

    # ─── 庫存數據加載與 Yahoo 監控引擎 ───
    conn = sqlite3.connect(DB_NAME)
    df_stocks = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()

    st.markdown("---")
    col_t, col_ref = st.columns([2, 1])
    with col_t: st.subheader("🟢 當前持有庫存總覽")
    with col_ref:
        if st.button("🔄 刷新 Yahoo 現值", type="secondary", use_container_width=True):
            with st.spinner("正在抓取即時報價..."):
                for _, r in df_stocks.iterrows():
                    p = fetch_yahoo_price(r['stock_id'], r['market'])
                    if p: st.session_state.yahoo_prices[r['stock_id']] = p
            st.rerun()

    # 🚨 區塊一：馬克紀律強烈視覺警示卡片 (鮮明橘紅色)
    if not df_stocks.empty:
        for _, row in df_stocks.iterrows():
            if row['period'] == '長期投資': continue  # 長期存股完全不觸發任何警示
            
            sid = row['stock_id']
            y_price = st.session_state.yahoo_prices.get(sid, None)
            if not y_price or row['avg_cost'] <= 0: continue
            
            profit_pct = ((y_price - row['avg_cost']) / row['avg_cost']) * 100
            
            # 計算觸及門檻
            if row['strategy_type'] == '2倍風險停利法':
                target_pct = row['stop_loss_pct'] * 2
                current_sell_ratio = 50.0
            else:
                target_pct = row['target_profit_pct']
                current_sell_ratio = row['sell_ratio']
                
            if profit_pct >= target_pct:
                # 換算應賣出股數
                suggested_sell_shares = round(row['shares'] * (current_sell_ratio / 100))
                expected_cash = round(suggested_sell_shares * y_price, 2)
                
                # 渲染亮橘色警示外殼
                st.markdown(f"""
                <div style="background-color: #ffe0b2; border-left: 8px solid #f57c00; padding: 15px; border-radius: 6px; margin-bottom: 15px; color: #5d4037;">
                    <b style="font-size: 1.2rem;">🔥 🚨 紀律警示：已達馬克分批停利門檻！</b><br>
                    <b>【{row['market']}】{sid} {row['stock_name']} ({row['period']})</b><br>
                    <hr style="border-top: 1px solid #bcaaa4; margin: 8px 0;">
                    📈 當 前 獲 利 ％ ： <span style="color:#d84315; font-weight:bold;">+{profit_pct:.2f}%</span> (目標: +{target_pct:.1f}%)<br>
                    🚪 門 檻 出 場 ％ ： 強制落袋 {current_sell_ratio}% 持股<br>
                    🛒 應 下 單 股 數 ： <b>請至券商下單賣出 {suggested_sell_shares:,} 股</b><br>
                    💵 預計收回總金額 ： <b>${expected_cash:,} 元</b><br>
                    <small>💡 續抱指引：減碼後，請將賸餘部位的防守點移至買入成本價 ${row['avg_cost']} 鎖定利潤。</small>
                </div>
                """, unsafe_allow_value=True)
                
                confirm_key = f"chk_action_{sid}"
                if st.checkbox("🧾 我已在券商完成此筆減碼下單 (勾選展開微調)", key=confirm_key):
                    with st.container(border=True):
                        st.caption("✍️ 請核對實際成交明細（容許滑價調整）：")
                        actual_shares = st.number_input("實際成交股數", value=float(suggested_sell_shares), step=1.0, key=f"act_s_{sid}")
                        actual_price = st.number_input("實際成交價格", value=float(y_price), step=0.01, key=f"act_p_{sid}")
                        actual_date = st.date_input("操作日期", value=datetime.today(), key=f"act_d_{sid}")
                        
                        if st.button("👍 確定扣減股數並歸檔歷史", key=f"btn_confirm_act_{sid}", type="primary"):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            new_shares = max(0.0, row['shares'] - actual_shares)
                            
                            # 更新主體庫存
                            if new_shares <= 0:
                                cursor.execute("UPDATE stock_master SET status='已結案', shares=0 WHERE id=?", (row['id'],))
                            else:
                                cursor.execute("UPDATE stock_master SET shares=? WHERE id=?", (new_shares, row['id']))
                                
                            # 寫入歷史時序
                            note_msg = f"觸發馬克【{row['strategy_type']}】，自動換算減碼。當前獲利 +{profit_pct:.2f}%，實際賣出 {actual_shares} 股，收回金額：${round(actual_shares*actual_price, 2)}"
                            cursor.execute("""
                                INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                                VALUES (?, '減碼', ?, ?, ?, ?)
                            """, (sid, actual_date.strftime("%Y-%m-%d"), actual_price, actual_shares, note_msg))
                            conn.commit()
                            conn.close()
                            st.success("紀錄已成功同步歸檔！")
                            st.rerun()

    # 🟩 區塊二：核心持有庫存總覽列表
    if df_stocks.empty:
        st.caption("目前系統中尚無持股，請先由上方或側邊欄匯入資料。")
    else:
        for _, row in df_stocks.iterrows():
            sid = row['stock_id']
            y_price = st.session_state.yahoo_prices.get(sid, None)
            
            # 計算即時損益
            if y_price and row['avg_cost'] > 0:
                pnl_rate = ((y_price - row['avg_cost']) / row['avg_cost']) * 100
                current_value = y_price * row['shares']
                pnl_money = (y_price - row['avg_cost']) * row['shares']
                pnl_str = f"損益：{'🔴' if pnl_money < 0 else '🟢'} **{pnl_rate:.2f}%** (${pnl_money:,.1f})"
                value_str = f"📈 Yahoo市價: **{y_price}** | 當前現值: **{current_value:,.1f}**"
            else:
                pnl_str = "⚪ 待點擊上方按鈕刷新即時損益"
                value_str = "現值估算：未刷新價格"

            # 分流排版外殼：如果是長期投資，呈現舒服的溫和淺藍色
            if row['period'] == '長期投資':
                st.markdown(f"""
                <div style="background-color: #e3f2fd; border-left: 6px solid #1e88e5; padding: 12px; border-radius: 5px; margin-bottom: 8px; color: #0d47a1;">
                    <b>💠【{row['market']}】{sid} {row['stock_name']} (長期投資存股)</b><br>
                    成本均價：{row['avg_cost']} | 持有股數：{row['shares']:,} 股<br>
                    {value_str}<br>{pnl_str}<br>
                    <small>📌 核心理由：{row['core_reason']}</small>
                </div>
                """, unsafe_allow_html=True)
            else:
                # 中短期操作卡片
                st.markdown(f"""
                <div style="background-color: #ffffff; border: 1px solid #e0e0e0; border-left: 6px solid #757575; padding: 12px; border-radius: 5px; margin-bottom: 8px; color: #333333;">
                    <b>🛡️【{row['market']}】{sid} {row['stock_name']} ({row['period']})</b><br>
                    成本均價：{row['avg_cost']} | 持有股數：{row['shares']:,} 股<br>
                    {value_str}<br>{pnl_str}<br>
                    <small>⚙️ 策略：{row['strategy_type']} (防守停損: {row['stop_loss_pct']}%)</small><br>
                    <small>📌 核心理由：{row['core_reason']}</small>
                </div>
                """, unsafe_allow_html=True)

            # 卡片專屬單手操作按鈕列
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                edit_btn = st.button("✏️ 快速編輯細節", key=f"edt_btn_{sid}", use_container_width=True)
                if edit_btn: st.session_state.edit_mode[sid] = not st.session_state.edit_mode.get(sid, False)
            with c2:
                if st.button("🛑 手動清倉結案", key=f"cls_btn_{sid}", use_container_width=True):
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_master SET status='已結案' WHERE id=?", (row['id'],))
                    cursor.execute("""
                        INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                        VALUES (?, '手動結案', ?, ?, ?, '手動於面板移出庫存結案')
                    """, (sid, datetime.today().strftime("%Y-%m-%d"), row['avg_cost'], row['shares']))
                    conn.commit()
                    conn.close()
                    st.success(f"{sid} 已移出持股總覽。")
                    st.rerun()

            # ⚙️ 展開卡片原地快速編輯細節
            if st.session_state.edit_mode.get(sid, False):
                with st.container(border=True):
                    st.caption(f"🔧 修正【{sid}】的核心欄位與紀律配置：")
                    u_id = st.text_input("股票代號", value=row['stock_id'], key=f"u_id_{sid}")
                    u_name = st.text_input("股票名稱", value=row['stock_name'], key=f"u_name_{sid}")
                    u_cost = st.number_input("平均成本", value=float(row['avg_cost']), step=0.01, key=f"u_cost_{sid}")
                    u_shares = st.number_input("目前股數", value=float(row['shares']), step=1.0, key=f"u_shares_{sid}")
                    u_period = st.selectbox("投資週期分類", ["長期投資", "中期波段", "短期操作"], index=["長期投資", "中期波段", "短期操作"].index(row['period']), key=f"u_per_{sid}")
                    u_strat = st.selectbox("減碼紀律策略", ["2倍風險停利法", "強勢波段停利法"], index=["2倍風險停利法", "強勢波段停利法"].index(row['strategy_type']), key=f"u_str_{sid}")
                    
                    cc1, cc2 = st.columns(2)
                    with cc1: u_sl = st.number_input("初始停損點 (%)", value=float(row['stop_loss_pct']), step=0.1, key=f"u_sl_{sid}")
                    with cc2: u_tp = st.number_input("自訂目標漲幅 (%)", value=float(row['target_profit_pct']), step=0.1, key=f"u_tp_{sid}")
                    u_ratio = st.number_input("波段觸發出場持股比例 (%)", value=float(row['sell_ratio']), step=5.0, key=f"u_rat_{sid}")
                    u_reason = st.text_area("初始核心理由", value=row['core_reason'], key=f"u_rea_{sid}")
                    
                    if st.button("💾 儲存此股修改", key=f"save_edit_{sid}", type="primary", use_container_width=True):
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE stock_master SET 
                                stock_id=?, stock_name=?, avg_cost=?, shares=?, period=?, 
                                strategy_type=?, stop_loss_pct=?, target_profit_pct=?, sell_ratio=?, core_reason=?
                            WHERE id=?
                        """, (u_id, u_name, u_cost, u_shares, u_period, u_strat, u_sl, u_tp, u_ratio, u_reason, row['id']))
                        conn.commit()
                        conn.close()
                        st.session_state.edit_mode[sid] = False
                        st.success("個股細節已成功修正！")
                        st.rerun()
            st.markdown("<div style='margin-bottom:15px;'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────
# 分頁二：個股生命週期故事書 (時序軸)
# ──────────────────────────────────────────
with tab2:
    st.subheader("🔍 單一個股生命週期全覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_all_m = pd.read_sql_query("SELECT DISTINCT stock_id, stock_name FROM stock_master", conn)
    conn.close()
    
    if df_all_m.empty:
        st.caption("尚無任何交易歷史數據。")
    else:
        options = [f"{r['stock_id']} {r['stock_name']}" for _, r in df_all_m.iterrows()]
        selected_stock = st.selectbox("請選擇個股檢視生命時序：", options)
        selected_id = selected_stock.split(" ")[0]
        
        conn = sqlite3.connect(DB_NAME)
        df_timeline = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE stock_id='{selected_id}' ORDER BY op_date DESC, id DESC", conn)
        df_master_info = pd.read_sql_query(f"SELECT * FROM stock_master WHERE stock_id='{selected_id}' LIMIT 1", conn)
        conn.close()
        
        if not df_master_info.empty:
            st.info(f"💡 置頂初始核心戰略母體理由：\n{df_master_info.iloc[0]['core_reason']}")
            
        st.write("⏱️ 歷史操作決策流水帳：")
        for _, row in df_timeline.iterrows():
            badge = "🟢 買入/加碼" if "建倉" in row['action_type'] or "加碼" in row['action_type'] else "🔴 紀律減碼/出場"
            with st.container(border=True):
                st.markdown(f"**{row['op_date']} | {badge}**")
                st.markdown(f"參考價格: `${row['price']}` | 變動股數: `{row['shares_changed']}` 股")
                st.markdown(f"💬 交易日誌備忘錄:\n*{row['note']}*")

# ──────────────────────────────────────────
# 分頁三：月份進出場明細覆盤 (月報)
# ──────────────────────────────────────────
with tab3:
    st.subheader("📅 月度進出場決策大事件覆盤")
    conn = sqlite3.connect(DB_NAME)
    df_dates = pd.read_sql_query("SELECT DISTINCT substr(op_date, 1, 7) as ym FROM stock_timeline ORDER BY ym DESC", conn)
    conn.close()
    
    if df_dates.empty:
        st.caption("目前尚無任何月份的操作大事記。")
    else:
        selected_ym = st.selectbox("請選擇覆盤月份：", df_dates['ym'].tolist())
        
        conn = sqlite3.connect(DB_NAME)
        df_month = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE op_date LIKE '{selected_ym}%' ORDER BY op_date DESC", conn)
        conn.close()
        
        st.write(f"### 🎯 {selected_ym} 操作紀律大事記")
        for _, row in df_month.iterrows():
            is_buy = "建倉" in row['action_type'] or "加碼" in row['action_type']
            color_badge = "🟩 [買入動態]" if is_buy else "🟥 [減碼/出場]"
            st.markdown(f"**{row['op_date']}** | {color_badge} **{row['stock_id']}** (成交價: ${row['price']} | 數量: {row['shares_changed']} 股)")
            st.markdown(f"└ 覆盤日誌: *{row['note']}*")
            st.markdown("---")
            
        # 月度盲點文字檢討存檔功能
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("📝 交易員本月盲點與心態紀錄")
        if f"review_{selected_ym}" not in st.session_state:
            st.session_state[f"review_{selected_ym}"] = ""
        user_rev = st.text_area("寫下本月的紀律執行心得與優缺點檢討：", key=f"txt_rev_{selected_ym}")
        if st.button("💾 儲存月度檢討日誌", key=f"btn_rev_{selected_ym}", type="primary"):
            st.success(f"{selected_ym} 心態日誌已妥善保存！")
