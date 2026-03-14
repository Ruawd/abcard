#!/usr/bin/env python3
"""
测试 - 浏览器全流程支付

用真实 Playwright 浏览器打开 Stripe hosted checkout 页面,
填写卡信息并提交。绕过 hCaptcha 的关键在于真实浏览器环境。

用法:
  python3 test_browser_payment.py                    # 使用最新凭证
  python3 test_browser_payment.py credentials.json   # 指定凭证
  python3 test_browser_payment.py --headless          # 无头模式
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
logging.getLogger("browser_payment").setLevel(logging.DEBUG)
logging.getLogger("http_client").setLevel(logging.DEBUG)
logger = logging.getLogger("test_browser")

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════
PROXY = "http://172.25.16.1:7897"

CARD_NUMBER = "4462220004624356"
CARD_EXP_MONTH = "03"
CARD_EXP_YEAR = "2029"
CARD_CVC = "173"
BILLING_NAME = "Test User"
BILLING_COUNTRY = "GB"
BILLING_ZIP = "N2 8EY"
BILLING_LINE1 = "Langley House"


def load_credentials(path=None):
    if path:
        return json.load(open(path))
    cred_files = sorted(glob.glob("test_outputs/credentials_*.json"))
    if not cred_files:
        print("没有找到保存的凭证")
        sys.exit(1)
    latest = cred_files[-1]
    logger.info(f"使用凭证: {latest}")
    return json.load(open(latest))


def main():
    args = [a for a in sys.argv[1:] if not a.endswith(".json") and not a.startswith("--")]
    cred_args = [a for a in sys.argv[1:] if a.endswith(".json")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    headless = "--headless" in flags
    cred_path = cred_args[0] if cred_args else None

    cred = load_credentials(cred_path)
    logger.info(f"邮箱: {cred['email']}")
    logger.info(f"卡号: {CARD_NUMBER[:4]} **** **** {CARD_NUMBER[-4:]}")

    from browser_payment import BrowserPayment

    bp = BrowserPayment(
        proxy=PROXY,      # 代理 + checkout.stripe.com = 无 hCaptcha
        headless=headless,
        slow_mo=80,
    )

    result = bp.run_full_flow(
        session_token=cred["session_token"],
        access_token=cred["access_token"],
        device_id=cred.get("device_id", ""),
        card_number=CARD_NUMBER,
        card_exp_month=CARD_EXP_MONTH,
        card_exp_year=CARD_EXP_YEAR,
        card_cvc=CARD_CVC,
        billing_name=BILLING_NAME,
        billing_country=BILLING_COUNTRY,
        billing_zip=BILLING_ZIP,
        billing_line1=BILLING_LINE1,
        billing_email=cred.get("email", ""),
        chatgpt_proxy=PROXY,      # ChatGPT API 走代理
        timeout=120,
    )

    # 保存结果
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = f"test_outputs/browser_pay_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"结果已保存: {out_path}")

    if result.get("success"):
        print(f"\n✅ 浏览器支付成功!")
    else:
        print(f"\n❌ 浏览器支付失败: {result.get('error')}")


if __name__ == "__main__":
    main()
