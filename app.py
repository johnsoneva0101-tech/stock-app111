import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import yfinance as yf

DB_NAME = "stock_notebook.db"

# --- 核心邏輯：庫存自動同步函數 (修正 BUG 關鍵) ---
def sync_inventory(sid):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 重新加總歷史帳
    cursor.execute("SELECT action_type, shares_changed FROM stock_timeline WHERE stock_id=?", (sid,))
    rows = cursor.fetchall()
    total = 0
    for act, s in rows:
        if act in ['初始建倉', '加碼']: total += s
        elif act in ['減碼', '已實現出場']: total -= s
    
    # 回寫母表
    if total <= 0:
        cursor.execute("UPDATE stock_master SET status='已結案', shares=0 WHERE stock_id=?", (sid,))
    else:
        cursor.execute("UPDATE stock_master SET shares=? WHERE stock_id=? AND status='持有'", (max(0, total), sid))
    conn.commit()
    conn.close()

# --- [理由模板套用邏輯修正] ---
def apply_template(note_key, template_text):
    st.session_state[note_key] = template_text

# --- (以下為 app.py 的其餘結構，重點請確保理由輸入處如下修正) ---

# 在加減碼與新增區域的理由框：
# 1. 定義 Key
note_key = f"note_tx_{db_id}"
if note_key not in st.session_state: st.session_state[note_key] = ""

# 2. 模板按鈕
cols = st.columns(len(pool))
for i, t in enumerate(pool):
    if cols[i].button(t[:6]+"...", key=f"btn_{db_id}_{i}"):
        st.session_state[note_key] = t # 更新狀態
        st.rerun() # 強制刷新畫面

# 3. 輸入框
tx_note = st.text_area("📝 詳細操作理由：", value=st.session_state[note_key], key=note_key)

# 4. 在刪除歷史紀錄的邏輯中，加入 sync_inventory(sid)
if st.button("🗑️ 刪除紀錄"):
    # ...執行刪除 SQL...
    sync_inventory(sid) # 關鍵：刪除後立刻同步
    st.rerun()
