#!/usr/bin/env python3
"""
测试 - 多策略绕过 hCaptcha

策略 A: 全部走代理 (IP 一致性 - ChatGPT + Stripe 全走同一代理)
策略 B: 全部直连 (测试 checkout 是否需要代理)
策略 C: ChatGPT 走代理 + Stripe 直连 (原始策略, 带优化captcha参数)

用法:
  python3 test_direct_payment.py A                     # 全走代理
  python3 test_direct_payment.py B                     # 全直连
  python3 test_direct_payment.py C                     # ChatGPT 代理 + Stripe 直连
  python3 test_direct_payment.py A credentials.json    # 指定凭证
"""
import logging
import json
import sys
import glob
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("payment_flow").setLevel(logging.DEBUG)
logging.getLogger("stripe_fingerprint").setLevel(logging.DEBUG)
logger = logging.getLogger("test_direct")

from config import Config, CardInfo, BillingInfo, CaptchaConfig
from auth_flow import AuthResult
from payment_flow import PaymentFlow

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════
PROXY = "http://172.25.16.1:7897"

CARD = CardInfo(
    number="4462220004624356",
    cvc="173",
    exp_month="03",
    exp_year="2029",
)
BILLING = BillingInfo(
    name="Test User",
    email="",
    country="GB",
    currency="GBP",
    address_line1="Langley House",
    address_state="London",
    postal_code="N2 8EY",
)
CAPTCHA = CaptchaConfig(
    api_url="https://api.yescaptcha.com",
    client_key="27e2aa9da9a236b2a6cfcc3fa0f045fdec2a3633104361",
)

# 策略定义: (chatgpt_proxy, stripe_proxy, description)
STRATEGIES = {
    "A": (PROXY, PROXY, "全代理 (IP 一致性)"),
    "B": (None, None, "全直连 (无代理)"),
    "C": (PROXY, None, "ChatGPT 代理 + Stripe 直连"),
}


def load_credentials(path: str = None) -> dict:
    if path:
        return json.load(open(path))
    cred_files = sorted(glob.glob("test_outputs/credentials_*.json"))
    if not cred_files:
        print("没有找到保存的凭证")
        sys.exit(1)
    latest = cred_files[-1]
    logger.info(f"使用凭证: {latest}")
    return json.load(open(latest))


def build_config_and_auth(cred: dict, chatgpt_proxy: str) -> tuple:
    auth = AuthResult()
    auth.email = cred["email"]
    auth.password = cred.get("password", "")
    auth.session_token = cred["session_token"]
    auth.access_token = cred["access_token"]
    auth.device_id = cred.get("device_id", "")

    cfg = Config()
    cfg.proxy = chatgpt_proxy
    cfg.card = CARD
    cfg.billing = BillingInfo(
        name=BILLING.name,
        email=auth.email,
        country=BILLING.country,
        currency=BILLING.currency,
        address_line1=BILLING.address_line1,
        address_state=BILLING.address_state,
        postal_code=BILLING.postal_code,
    )
    cfg.captcha = CAPTCHA
    return cfg, auth


def run_strategy(strategy_key: str, cred: dict) -> dict:
    chatgpt_proxy, stripe_proxy, desc = STRATEGIES[strategy_key]
    logger.info("=" * 60)
    logger.info(f"策略 {strategy_key}: {desc}")
    logger.info(f"  ChatGPT proxy: {chatgpt_proxy or '直连'}")
    logger.info(f"  Stripe proxy:  {stripe_proxy or '直连'}")
    logger.info("=" * 60)

    cfg, auth = build_config_and_auth(cred, chatgpt_proxy)
    pf = PaymentFlow(cfg, auth, stripe_proxy=stripe_proxy)
    result = pf.run_payment()

    logger.info(f"策略 {strategy_key} 结果: success={result.success}, error={result.error}")
    out = result.to_dict()
    out["strategy"] = strategy_key
    out["strategy_desc"] = desc
    return out


def main():
    # 解析参数
    args = [a for a in sys.argv[1:] if not a.endswith(".json")]
    cred_args = [a for a in sys.argv[1:] if a.endswith(".json")]
    strategy_key = args[0].upper() if args else "A"
    cred_path = cred_args[0] if cred_args else None

    if strategy_key not in STRATEGIES:
        print(f"未知策略: {strategy_key}")
        print(f"可用: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)

    cred = load_credentials(cred_path)
    logger.info(f"邮箱: {cred['email']}")
    logger.info(f"卡号: {CARD.number[:4]} **** **** {CARD.number[-4:]}")

    result = run_strategy(strategy_key, cred)

    # 保存结果
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = f"test_outputs/strategy_{strategy_key}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"结果已保存: {out_path}")

    if result.get("success"):
        print(f"\n✅ 策略 {strategy_key} 支付成功!")
    else:
        print(f"\n❌ 策略 {strategy_key} 失败: {result.get('error')}")


if __name__ == "__main__":
    main()
