"""
自动化绑卡支付 - Streamlit UI
运行: streamlit run ui.py --server.address 0.0.0.0 --server.port 8501
"""
import csv
import json
import logging
import os
import sys
import traceback
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, CardInfo, BillingInfo
from mail_provider import MailProvider
from auth_flow import AuthFlow, AuthResult
from payment_flow import PaymentFlow
from logger import ResultStore

OUTPUT_DIR = "test_outputs"

st.set_page_config(page_title="Auto BindCard", page_icon="💳", layout="wide")

st.markdown("""
<style>
    section[data-testid="stSidebar"] .stTextInput > div > div > input { padding: 0.3rem 0.5rem; }
</style>
""", unsafe_allow_html=True)


# ── 日志 ──
class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        if "log_buffer" in st.session_state:
            st.session_state.log_buffer.append(self.format(record))


def init_logging():
    handler = LogCapture()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [h for h in root.handlers if not isinstance(h, LogCapture)]
    root.addHandler(handler)


# ── Session State ──
for k, v in {"log_buffer": [], "running": False, "result": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ════════════════════════════════════════
# 侧边栏
# ════════════════════════════════════════
with st.sidebar:
    st.title("⚙️ 配置")

    st.subheader("流程")
    do_register = st.checkbox("注册账号", value=True)
    do_checkout = st.checkbox("创建 Checkout", value=True)
    do_payment = st.checkbox("提交支付", value=False, help="需要真实信用卡")

    st.divider()
    proxy = st.text_input("🌐 代理", placeholder="socks5://127.0.0.1:1080")

    st.divider()
    with st.expander("📧 邮箱", expanded=False):
        mail_worker = st.text_input("Worker", value="https://apimail.mkai.de5.net")
        mail_domain = st.text_input("域名", value="mkai.de5.net")
        mail_token = st.text_input("Token", value="ma123999", type="password")

    with st.expander("📋 Team Plan", expanded=False):
        workspace_name = st.text_input("Workspace", value="Artizancloud")
        seat_quantity = st.number_input("席位", min_value=2, max_value=50, value=5)
        promo_campaign = st.text_input("促销 ID", value="team1dollar")

    with st.expander("💰 账单", expanded=False):
        c1, c2 = st.columns(2)
        country = c1.selectbox("国家", ["JP", "US", "SG", "HK", "GB"])
        currency = c2.selectbox("货币", ["JPY", "USD", "SGD", "HKD", "GBP"])
        billing_name = st.text_input("姓名", value="Test User")
        address_line1 = st.text_input("地址", value="1-1-1 Shibuya")
        address_state = st.text_input("州/省", value="Tokyo")

    if do_payment:
        with st.expander("💳 信用卡", expanded=True):
            card_number = st.text_input("卡号", placeholder="真实卡号")
            c1, c2, c3 = st.columns(3)
            exp_month = c1.text_input("月", value="12")
            exp_year = c2.text_input("年", value="2030")
            card_cvc = c3.text_input("CVC", type="password")
            st.warning("⚠️ Live 模式，真实扣款")


# ════════════════════════════════════════
# 主界面
# ════════════════════════════════════════
st.title("💳 Auto BindCard")

steps = []
if do_register: steps.append("注册")
if do_checkout: steps.append("Checkout")
if do_payment: steps.append("支付")
st.caption(" → ".join(steps) if steps else "请选择流程步骤")

tab_run, tab_accounts, tab_history = st.tabs(["▶ 执行", "📋 账号", "📊 历史"])

# ════════════════════════════════════════
# Tab: 执行
# ════════════════════════════════════════
with tab_run:
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        run_btn = st.button("🚀 开始", disabled=st.session_state.running or not steps, use_container_width=True, type="primary")
    with c2:
        if st.button("🗑️ 清空", use_container_width=True):
            st.session_state.log_buffer = []
            st.session_state.result = None
            st.rerun()
    with c3:
        if st.session_state.result:
            if st.session_state.result.get("success"):
                st.success("✅")
            else:
                st.error("❌")

    if run_btn:
        st.session_state.running = True
        st.session_state.log_buffer = []
        st.session_state.result = None
        init_logging()

        status = st.empty()
        pbar = st.progress(0)
        log_area = st.empty()

        store = ResultStore(output_dir=OUTPUT_DIR)
        rd = {"success": False, "error": "", "email": "", "steps": {}}

        try:
            cfg = Config()
            cfg.proxy = proxy or None
            cfg.mail.email_domain = mail_domain
            cfg.mail.worker_domain = mail_worker
            cfg.mail.admin_token = mail_token
            cfg.team_plan.workspace_name = workspace_name
            cfg.team_plan.seat_quantity = seat_quantity
            cfg.team_plan.promo_campaign_id = promo_campaign
            cfg.billing = BillingInfo(name=billing_name, email="", country=country, currency=currency,
                                      address_line1=address_line1, address_state=address_state)
            if do_payment:
                cfg.card = CardInfo(number=card_number, cvc=card_cvc, exp_month=exp_month, exp_year=exp_year)

            auth_result = None
            af = None

            # ── 注册 ──
            if do_register:
                status.info("⏳ 注册中...")
                pbar.progress(5)
                mp = MailProvider(worker_domain=cfg.mail.worker_domain, admin_token=cfg.mail.admin_token, email_domain=cfg.mail.email_domain)
                af = AuthFlow(cfg)
                auth_result = af.run_register(mp)
                rd["email"] = auth_result.email
                rd["steps"]["register"] = "✅"
                pbar.progress(40)
                status.success(f"✅ 注册: {auth_result.email}")
                store.save_credentials(auth_result.to_dict())
                store.append_credentials_csv(auth_result.to_dict())
                log_area.code("\n".join(st.session_state.log_buffer[-80:]), language="log")

            # ── Checkout ──
            if do_checkout:
                if not auth_result:
                    raise RuntimeError("需先注册或提供凭证")
                status.info("⏳ Checkout...")
                pbar.progress(50)
                cfg.billing.email = auth_result.email
                pf = PaymentFlow(cfg, auth_result)
                if af:
                    pf.session = af.session

                cs_id = pf.create_checkout_session()
                pf.fetch_stripe_fingerprint()
                pf.extract_stripe_pk(pf.checkout_url)
                rd["checkout_session_id"] = cs_id
                rd["stripe_pk"] = (pf.stripe_pk[:30] + "...") if pf.stripe_pk else ""
                rd["steps"]["checkout"] = "✅"
                rd["steps"]["fingerprint"] = "✅"
                pbar.progress(70)
                status.success(f"✅ Checkout: {cs_id[:35]}...")
                log_area.code("\n".join(st.session_state.log_buffer[-80:]), language="log")

                # ── 支付 ──
                if do_payment:
                    status.info("⏳ 支付...")
                    pbar.progress(80)
                    pf.payment_method_id = pf.create_payment_method()
                    rd["steps"]["tokenize"] = "✅"
                    pbar.progress(90)
                    pay = pf.confirm_payment(cs_id)
                    rd["confirm_status"] = pay.confirm_status
                    rd["confirm_response"] = pay.confirm_response
                    rd["success"] = pay.success
                    rd["error"] = pay.error
                    rd["steps"]["confirm"] = "✅" if pay.success else f"❌ {pay.error}"
                else:
                    rd["success"] = True
            elif do_register:
                rd["success"] = True

            pbar.progress(100)
            if rd["success"]:
                status.success(f"✅ 完成! {rd.get('email', '')}")
            else:
                status.warning(f"⚠️ {rd.get('error', '')}")

        except Exception as e:
            rd["error"] = str(e)
            st.session_state.log_buffer.append(f"EXCEPTION:\n{traceback.format_exc()}")
            status.error(f"❌ {e}")

        st.session_state.result = rd
        st.session_state.running = False

        try:
            store.save_result(rd, "ui_run")
            if rd.get("email"):
                store.append_history(email=rd["email"], status="ui_run",
                                     checkout_session_id=rd.get("checkout_session_id", ""),
                                     payment_status=rd.get("confirm_status", ""),
                                     error=rd.get("error", ""))
        except Exception:
            pass

        log_area.code("\n".join(st.session_state.log_buffer[-200:]), language="log")

    # ── 已有日志 ──
    elif st.session_state.log_buffer:
        st.code("\n".join(st.session_state.log_buffer[-200:]), language="log")

    # ── 结果卡片 ──
    if st.session_state.result and not run_btn:
        r = st.session_state.result
        cols = st.columns(4)
        cols[0].metric("邮箱", r.get("email") or "-")
        cols[1].metric("Checkout", (r.get("checkout_session_id", "")[:18] + "...") if r.get("checkout_session_id") else "-")
        cols[2].metric("Confirm", r.get("confirm_status") or "-")
        cols[3].metric("状态", "✅" if r.get("success") else "❌")

        if r.get("steps"):
            for step, val in r["steps"].items():
                st.text(f"  {step}: {val}")

        with st.expander("JSON", expanded=False):
            st.json(r)


# ════════════════════════════════════════
# Tab: 账号
# ════════════════════════════════════════
with tab_accounts:
    csv_path = os.path.join(OUTPUT_DIR, "accounts.csv")
    if os.path.exists(csv_path):
        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            if not df.empty:
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.caption(f"{len(df)} 条")
                if st.button("🔄 刷新", key="ref_acc"):
                    st.rerun()
            else:
                st.info("暂无")
        except Exception as e:
            st.error(str(e))
    else:
        st.info("暂无。注册后自动保存。")

    st.divider()
    st.markdown("**凭证文件**")
    if os.path.exists(OUTPUT_DIR):
        cred_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.startswith("credentials_") and f.endswith(".json")], reverse=True)
        if cred_files:
            sel = st.selectbox("选择", cred_files, key="cred_sel")
            if sel:
                with open(os.path.join(OUTPUT_DIR, sel)) as f:
                    data = json.load(f)
                st.json({k: (v[:50] + "..." + v[-20:] if isinstance(v, str) and len(v) > 80 else v) for k, v in data.items()})


# ════════════════════════════════════════
# Tab: 历史
# ════════════════════════════════════════
with tab_history:
    hist_path = os.path.join(OUTPUT_DIR, "history.csv")
    if os.path.exists(hist_path):
        try:
            import pandas as pd
            df = pd.read_csv(hist_path)
            if not df.empty:
                st.dataframe(df, use_container_width=True, hide_index=True)
                if st.button("🔄 刷新", key="ref_hist"):
                    st.rerun()
            else:
                st.info("暂无")
        except Exception as e:
            st.error(str(e))
    else:
        st.info("暂无")

    st.divider()
    st.markdown("**结果文件**")
    if os.path.exists(OUTPUT_DIR):
        rf = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json") and not f.startswith("credentials_") and not f.startswith("debug_")], reverse=True)
        if rf:
            sel = st.selectbox("选择", rf, key="res_sel")
            if sel:
                with open(os.path.join(OUTPUT_DIR, sel)) as f:
                    st.json(json.load(f))
