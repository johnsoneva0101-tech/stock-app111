import streamlit as st
import pandas as pd
import sqlite3
import io
from datetime import datetime

# ==========================================
# 1. 系統常數與理由模板定義
# ==========================================
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
# 2. 資料庫初始化與核心存取
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立母體表 (Master)
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
    # 建立時序流水帳表 (Timeline)
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
# 3. 券商 CSV 自動解析邏輯
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
        return pd.DataFrame()
        
    df.columns = [str(c).strip() for c in df.columns]
    parsed_data = []
    
    # 判定美股複委託庫存
    if '代號' in df.columns and '均價' in df.columns and '目前庫存' in df.columns:
        for _, row in df.iterrows():
            if pd.isna(row['代號']) or str(row['代號']).strip() == '':
                continue
            parsed_data.append({
                'market': '美股', 'stock_id': str(row['代號']).strip(),
                'stock_name': str(row['股票名稱']).strip(),
                'avg_cost': float(str(row['均價']).replace(',', '')),
                'shares': float(str(row['目前庫存']).replace(',', ''))
            })
    # 判定台股未實現彙總
    elif '股票名稱' in df.columns and '成交均價' in df.columns and '股數' in df.columns:
        for _, row in df.iterrows():
            name = str(row['股票名稱']).strip()
            if '總預估' in name or '總融資' in name or name == '': 
                continue
            stock_id = name.split(" ")[0] if " " in name else name
            stock_name = name.split(" ", 1)[1] if " " in name else name
            parsed_data.append({
                'market': '台股', 'stock_id': stock_id, 'stock_name': stock_name,
                'avg_cost': float(str(row['成交均價']).replace(',', '')),
                'shares': float(str(row['股數']).replace(',', ''))
            })
    return pd.DataFrame(parsed_data)

# ==========================================
# 4. 庫存比對引擎 (核心事件驅動)
# ==========================================
def check_inventory_changes(df_new):
    if df_new.empty:
        return []
    
    conn = sqlite3.connect(DB_NAME)
    df_old = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()
    
    events = []
    
    # 建立舊庫存索引字典方便比對
    old_dict = {row['stock_id']: row for _, row in df_old.iterrows()}
    new_stock_ids = set()
    
    for _, row in df_new.iterrows():
        sid = row['stock_id']
        new_stock_ids.add(sid)
        
        if sid not in old_dict:
            # 歷史上完全沒這檔股票 -> 初始建倉
            events.append({
                'type': '初始建倉', 'market': row['market'], 'stock_id': sid, 'stock_name': row['stock_name'],
                'old_shares': 0, 'new_shares': row['shares'], 'old_cost': 0, 'new_cost': row['avg_cost']
            })
        else:
            old_item = old_dict[sid]
            # 股數增加 -> 加碼
            if row['shares'] > old_item['shares']:
                events.append({
                    'type': '加碼', 'market': old_item['market'], 'stock_id': sid, 'stock_name': old_item['stock_name'],
                    'old_shares': old_item['shares'], 'new_shares': row['shares'],
                    'old_cost': old_item['avg_cost'], 'new_cost': row['avg_cost']
                })
            # 股數減少 -> 減碼
            elif row['shares'] < old_item['shares']:
                events.append({
                    'type': '減碼', 'market': old_item['market'], 'stock_id': sid, 'stock_name': old_item['stock_name'],
                    'old_shares': old_item['shares'], 'new_shares': row['shares'],
                    'old_cost': old_item['avg_cost'], 'new_cost': row['avg_cost']
                })
                
    # 檢查有哪些股票在新 CSV 裡消失了 -> 全數賣出
    for sid, old_item in old_dict.items():
        if sid not in new_stock_ids:
            events.append({
                'type': '全數賣出', 'market': old_item['market'], 'stock_id': sid, 'stock_name': old_item['stock_name'],
                'old_shares': old_item['shares'], 'new_shares': 0, 'old_cost': old_item['avg_cost'], 'new_cost': 0
            })
            
    return events

# ==========================================
# 5. 手機優化網頁介面 (RWD)
# ==========================================
st.set_page_config(page_title="策略筆記本", layout="centered") # 採 centered 佈局最適合手機單欄閱讀

# 手機頂端精簡標題
st.title("📱 投資決策筆記本")

# 建立手機版三大分頁
tab1, tab2, tab3 = st.tabs(["📊 今日動態/總覽", "🔍 個股時序", "📅 月份回顧"])

# ------------------------------------------
# 分頁一：今日動態與總覽
# ------------------------------------------
with tab1:
    st.subheader("📥 匯入最新庫存")
    uploaded_file = st.file_uploader("上傳券商庫存 CSV 檔案", type=["csv"], label_visibility="collapsed")
    
    if uploaded_file:
        df_parsed = parse_uploaded_csv(uploaded_file)
        if not df_parsed.empty:
            # 觸發比對引導
            events = check_inventory_changes(df_parsed)
            
            if not events:
                st.info("💡 庫存數據與上次相同，無新增變動事件。")
            else:
                st.warning(f"⚠️ 偵測到 {len(events)} 筆庫存變動事件！請確認並記錄理由：")
                
                # 遍歷每個變動事件，在手機上呈現大卡片型態供單手操作
                for idx, ev in enumerate(events):
                    with st.expander(f"【{ev['type']}】{ev['stock_id']} {ev['stock_name']}", expanded=True):
                        st.write(f"• 股數：{ev['old_shares']} ➔ {ev['new_shares']}")
                        st.write(f"• 均價：${ev['old_cost']} ➔ ${ev['new_cost']}")
                        
                        # 1. 日期調整（解決非即時上傳 Bug）
                        op_date = st.date_input("📅 操作日期 (可點擊微調)", value=datetime.today(), key=f"date_{idx}")
                        op_date_str = op_date.strftime("%Y-%m-%d")
                        
                        # 2. 理由模板快速選取群組
                        st.write("⚡ 快捷理由模板分類：")
                        pool = TEMPLATES["🟢 買入/加碼"] if ev['type'] in ['初始建倉', '加碼'] else TEMPLATES["🔴 賣出/減碼"]
                        
                        # 初始化或取得目前輸入框的文字內容
                        input_key = f"note_{idx}"
                        if input_key not in st.session_state:
                            st.session_state[input_key] = ""
                            
                        # 在手機上橫向排列或分行排列大按鈕
                        for template_text in pool:
                            if st.button(template_text[:12] + "...", key=f"btn_{idx}_{template_text[:5]}"):
                                st.session_state[input_key] = template_text
                                
                        # 3. 實際理由文字框
                        note_text = st.text_area("📝 操作詳細理由筆記：", value=st.session_state[input_key], key=input_key)
                        
                        # 4. 寫入按鈕
                        if st.button("💾 確認寫入歷史流水帳", key=f"save_{idx}", type="primary"):
                            conn = sqlite3.connect(DB_NAME)
                            cursor = conn.cursor()
                            
                            # 寫入流水帳表
                            shares_diff = abs(ev['new_shares'] - ev['old_shares'])
                            cursor.execute("""
                                INSERT INTO stock_timeline (stock_id, action_type, op_date, price, shares_changed, note)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (ev['stock_id'], ev['type'], op_date_str, ev['new_cost'], shares_diff, note_text))
                            
                            # 更新或更動母體表狀態
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
                                cursor.execute("""
                                    UPDATE stock_master SET status = '已結案' WHERE stock_id = ? AND status = '持有'
                                """, (ev['stock_id']))
                                
                            conn.commit()
                            conn.close()
                            st.success(f"【{ev['stock_id']}】流水帳紀錄成功！")
                            st.rerun()
        else:
            st.error("無法識別此 CSV 欄位架構。")
            
    # 下方顯示當前持股清單（手機卡片式設計）
    st.markdown("---")
    st.subheader("🟢 當前持有庫存總覽")
    conn = sqlite3.connect(DB_NAME)
    df_masters = pd.read_sql_query("SELECT * FROM stock_master WHERE status='持有'", conn)
    conn.close()
    
    if df_masters.empty:
        st.caption("目前系統中尚無庫存資料，請由上方上傳 CSV 觸發。")
    else:
        for _, row in df_masters.iterrows():
            with st.container(border=True):
                st.markdown(f"**[{row['market']}] {row['stock_id']} {row['stock_name']}**")
                st.markdown(f"均價: `{row['avg_cost']}` | 持有股數: `{row['shares']}`")
                st.caption(f"📌 初始核心理由: {row['core_reason']}")

# ------------------------------------------
# 分頁二：個股時序 (故事書)
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
        
        # 撈取該股票流水帳
        conn = sqlite3.connect(DB_NAME)
        df_timeline = pd.read_sql_query(f"SELECT * FROM stock_timeline WHERE stock_id='{selected_id}' ORDER BY op_date DESC", conn)
        df_m_info = pd.read_sql_query(f"SELECT core_reason FROM stock_master WHERE stock_id='{selected_id}' LIMIT 1", conn)
        conn.close()
        
        if not df_m_info.empty:
            st.info(f"💡 置頂核心戰略母體理由：\n{df_m_info.iloc[0]['core_reason']}")
            
        st.write("⏱️ 歷史操作流水帳：")
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
            badge = "🟩 [買入/加碼]" if "買" in row['action_type'] or "加" in row['action_type'] else "🟥 [賣出/減碼]"
            st.markdown(f"**{row['op_date']}** | {badge} **{row['stock_id']}**")
            st.markdown(f"└ 理由: *{row['note']}*")
            st.markdown("---")