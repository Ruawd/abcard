"""
自动化绑卡支付 - Streamlit UI
运行: streamlit run ui.py --server.address 0.0.0.0 --server.port 8501
"""
import csv
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from io import StringIO

import streamlit as st

# 确保项目根目录在 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, CardInfo, BillingInfo, TeamPlanConfig
from mail_provider import MailProvider
from auth_flow import AuthFlow, AuthResult
from payment_flow import PaymentFlow
from logger import ResultStore

# ── 页面配置 ──
st.set_page_config(page_title="Auto BindCard", page_icon="💳", layout="wide")


# ── 日志捕获 ──
class StreamlitLogHandler(logging.Handler):
    """将日志写入 Streamlit session 的日志缓冲区"""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        if "log_buffer" in st.session_state:
            st.session_state.log_buffer.append(msg)


def setup_ui_logging():
    handler = StreamlitLogHandler()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # 移除旧的 StreamlitLogHandler
    root.handlers = [h for h in root.handlers if not isinstance(h, StreamlitLogHandler)]
    root.addHandler(handler)


# ── 初始化 session state ──
if "log_buffer" not in st.session_state:
    st.session_state.log_buffer = []
if "running" not in st.session_state:
    st.session_state.running = False
if "result" not in st.session_state:
    st.session_state.result = None
if "auth_result" not in st.session_state:
    st.session_state.auth_result = None


# ── 侧边栏: 配置 ──
st.sidebar.title("⚙️ 配置")

# 代理
proxy = st.sidebar.text_input("代理 (可选)", placeholder="socks5://127.0.0.1:1080")

# 流程选择
st.sidebar.markdown("---")
st.sidebar.subheader("🔄 流程控制")
do_register = st.sidebar.checkbox("注册账号", value=True)
do_checkout = st.sidebar.checkbox("创建 Checkout Session", value=True)
do_payment = st.sidebar.checkbox("提交支付确认", value=False, help="需要真实信用卡")

# 邮箱配置
st.sidebar.markdown("---")
st.sidebar.subheader("📧 邮箱")
mail_domain = st.sidebar.text_input("邮箱域名", value="mkai.de5.net")
mail_worker = st.sidebar.text_input("Worker 域名", value="https://apimail.mkai.de5.net")
mail_token = st.sidebar.text_input("Admin Token", value="ma123999", type="password")

# Team Plan
st.sidebar.markdown("---")
st.sidebar.subheader("📋 Team Plan")
workspace_name = st.sidebar.text_input("Workspace 名称", value="Artizancloud")
seat_quantity = st.sidebar.number_input("席位数", min_value=2, max_value=50, value=5)
promo_campaign = st.sidebar.text_input("促销 ID", value="team1dollar")

# 账单信息
st.sidebar.markdown("---")
st.sidebar.subheader("💰 账单")
col1, col2 = st.sidebar.columns(2)
country = col1.selectbox("国家", ["JP", "US", "SG", "HK", "GB"], index=0)
currency = col2.selectbox("货币", ["JPY", "USD", "SGD", "HKD", "GBP"], index=0)
billing_name = st.sidebar.text_input("姓名", value="Test User")
address_line1 = st.sidebar.text_input("地址", value="1-1-1 Shibuya")
address_state = st.sidebar.text_input("州/省", value="Tokyo")

# 卡片信息 (仅支付时需要)
if do_payment:
    st.sidebar.markdown("---")
    st.sidebar.subheader("💳 信用卡")
    card_number = st.sidebar.text_input("卡号", placeholder="4242424242424242")
    c1, c2, c3 = st.sidebar.columns(3)
    exp_month = c1.text_input("月", value="12")
    exp_year = c2.text_input("年", value="2030")
    card_cvc = c3.text_input("CVC", value="123", type="password")


# ── 主界面 ──
st.title("💳 自动化绑卡支付")

# 流程描述
steps_desc = []
if do_register:
    steps_desc.append("注册")
if do_checkout:
    steps_desc.append("Checkout")
if do_payment:
    steps_desc.append("支付确认")
st.caption(f"流程: {' → '.join(steps_desc) if steps_desc else '未选择任何步骤'}")

# ── Tab 布局 ──
tab_run, tab_accounts, tab_history = st.tabs(["▶ 执行", "📋 账号列表", "📊 历史记录"])

with tab_run:
    # 执行按钮
    col_btn, col_status = st.columns([1, 3])
    with col_btn:
        run_clicked = st.button("🚀 开始执行", disabled=st.session_state.running, use_container_width=True)
        clear_log = st.button("🗑️ 清空日志", use_container_width=True)

    if clear_log:
        st.session_state.log_buffer = []
        st.rerun()

    with col_status:
        if st.session_state.running:
            st.info("⏳ 正在执行...")
        elif st.session_state.result:
            r = st.session_state.result
            if r.get("success"):
                st.success(f"✅ 完成 - {r.get('email', '')}")
            else:
                st.error(f"❌ 失败 - {r.get('error', '未知错误')}")

    # 执行逻辑
    if run_clicked and not st.session_state.running:
        st.session_state.running = True
        st.session_state.log_buffer = []
        st.session_state.result = None
        setup_ui_logging()

        progress = st.progress(0, text="初始化...")
        log_area = st.empty()

        store = ResultStore(output_dir="test_outputs")

        try:
            cfg = Config()
            cfg.proxy = proxy or None
            cfg.mail.email_domain = mail_domain
            cfg.mail.worker_domain = mail_worker
            cfg.mail.admin_token = mail_token
            cfg.team_plan.workspace_name = workspace_name
            cfg.team_plan.seat_quantity = seat_quantity
            cfg.team_plan.promo_campaign_id = promo_campaign
            cfg.billing = BillingInfo(
                name=billing_name, email="",
                country=country, currency=currency,
                address_line1=address_line1, address_state=address_state,
            )
            if do_payment:
                cfg.card = CardInfo(
                    number=card_number, cvc=card_cvc,
                    exp_month=exp_month, exp_year=exp_year,
                )

            result_data = {"success": False, "error": "", "email": ""}
            auth_result = None

            # ─ 注册 ─
            if do_register:
                progress.progress(10, text="注册中...")
                mp = MailProvider(
                    worker_domain=cfg.mail.worker_domain,
                    admin_token=cfg.mail.admin_token,
                    email_domain=cfg.mail.email_domain,
                )
                af = AuthFlow(cfg)
                auth_result = af.run_register(mp)
                result_data["email"] = auth_result.email
                result_data["password"] = auth_result.password
                progress.progress(40, text=f"注册成功: {auth_result.email}")

                # 保存凭证
                store.save_credentials(auth_result.to_dict())
                store.append_credentials_csv(auth_result.to_dict())
                st.session_state.auth_result = auth_result.to_dict()

            # ─ Checkout ─
            if do_checkout:
                if not auth_result:
                    raise RuntimeError("需要先注册或提供已有凭证")

                progress.progress(50, text="创建 Checkout Session...")
                cfg.billing.email = auth_result.email
                pf = PaymentFlow(cfg, auth_result)
                if do_register:
                    pf.session = af.session  # 共享 session

                cs_id = pf.create_checkout_session()
                pf.fetch_stripe_fingerprint()
                pf.extract_stripe_pk(pf.checkout_url)
                result_data["checkout_session_id"] = cs_id
                result_data["stripe_pk"] = pf.stripe_pk[:30] + "..."
                progress.progress(70, text=f"Checkout: {cs_id[:30]}...")

                # ─ 支付确认 ─
                if do_payment:
                    progress.progress(80, text="卡片 Tokenization...")
                    pf.payment_method_id = pf.create_payment_method()
                    progress.progress(90, text="确认支付...")
                    pay_result = pf.confirm_payment(cs_id)
                    result_data["confirm_status"] = pay_result.confirm_status
                    result_data["confirm_response"] = pay_result.confirm_response
                    result_data["success"] = pay_result.success
                    result_data["error"] = pay_result.error
                else:
                    result_data["success"] = True
            elif do_register:
                result_data["success"] = True

            progress.progress(100, text="完成!")

        except Exception as e:
            result_data["error"] = str(e)
            import traceback
            st.session_state.log_buffer.append(f"ERROR: {traceback.format_exc()}")

        st.session_state.result = result_data
        st.session_state.running = False

        # 保存结果
        try:
            store.save_result(result_data, "ui_run")
        except Exception:
            pass

        st.rerun()

    # 日志显示
    if st.session_state.log_buffer:
        st.markdown("#### 📝 执行日志")
        log_text = "\n".join(st.session_state.log_buffer[-200:])  # 最近 200 行
        st.code(log_text, language="log")

    # 结果详情
    if st.session_state.result:
        st.markdown("#### 📊 执行结果")
        st.json(st.session_state.result)

with tab_accounts:
    st.markdown("#### 📋 已注册账号")
    csv_path = os.path.join("test_outputs", "accounts.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"共 {len(rows)} 条记录")
        else:
            st.info("暂无账号记录")
    else:
        st.info("暂无账号记录。执行注册后会自动保存。")

    # 加载凭证 JSON 文件
    st.markdown("---")
    st.markdown("#### 📁 凭证文件")
    cred_dir = "test_outputs"
    if os.path.exists(cred_dir):
        cred_files = sorted(
            [f for f in os.listdir(cred_dir) if f.startswith("credentials_") and f.endswith(".json")],
            reverse=True,
        )
        if cred_files:
            selected = st.selectbox("选择凭证文件", cred_files)
            if selected:
                with open(os.path.join(cred_dir, selected)) as f:
                    cred_data = json.load(f)
                # 截断显示 token
                display = {k: (v[:50] + "..." if isinstance(v, str) and len(v) > 60 else v) for k, v in cred_data.items()}
                st.json(display)
        else:
            st.info("暂无凭证文件")

with tab_history:
    st.markdown("#### 📊 操作历史")
    hist_path = os.path.join("test_outputs", "history.csv")
    if os.path.exists(hist_path):
        with open(hist_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("暂无历史记录")
    else:
        st.info("暂无历史记录。执行操作后会自动保存。")

    # 结果文件
    st.markdown("---")
    st.markdown("#### 📁 结果文件")
    if os.path.exists("test_outputs"):
        result_files = sorted(
            [f for f in os.listdir("test_outputs")
             if (f.startswith("full_integration_") or f.startswith("ui_run_")) and f.endswith(".json")],
            reverse=True,
        )
        if result_files:
            selected = st.selectbox("选择结果文件", result_files, key="result_file")
            if selected:
                with open(os.path.join("test_outputs", selected)) as f:
                    st.json(json.load(f))
