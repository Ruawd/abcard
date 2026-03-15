"""
邮箱服务 - Cloud Mail (Ruawd/cloud-mail)
通过 Cloud Mail 的开放 API 创建用户并接收 OTP 验证码
"""
import re
import time
import random
import string
import logging

from http_client import create_http_session

logger = logging.getLogger(__name__)


class MailProvider:
    """Cloud Mail 邮箱提供者"""

    def __init__(self, worker_domain: str, admin_email: str, admin_password: str, email_domain: str):
        self.worker_domain = worker_domain.rstrip("/")
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.email_domain = email_domain
        self.session = create_http_session()
        self.api_token: str | None = None
        # 当前创建的邮箱密码（用于 addUser 接口）
        self._current_password: str = ""

    def _random_name(self) -> str:
        """生成随机邮箱前缀"""
        letters1 = "".join(random.choices(string.ascii_lowercase, k=5))
        numbers = "".join(random.choices(string.digits, k=random.randint(1, 3)))
        letters2 = "".join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
        return letters1 + numbers + letters2

    def _gen_token(self):
        """调用 Cloud Mail API 生成 public token"""
        resp = self.session.post(
            f"{self.worker_domain}/api/public/genToken",
            json={"email": self.admin_email, "password": self.admin_password},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 200:
            raise RuntimeError(f"生成 Token 失败: {result.get('message', result)}")
        self.api_token = result["data"]["token"]
        logger.info("Cloud Mail API Token 已获取")

    def _ensure_token(self):
        """确保 API Token 可用"""
        if not self.api_token:
            self._gen_token()

    def create_mailbox(self) -> str:
        """创建临时邮箱，返回邮箱地址"""
        self._ensure_token()
        name = self._random_name()
        email = f"{name}@{self.email_domain}"
        # 生成随机密码用于创建用户
        self._current_password = "".join(
            random.choices(string.ascii_letters + string.digits, k=16)
        )
        headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
        }
        resp = self.session.post(
            f"{self.worker_domain}/api/public/addUser",
            json={"list": [{"email": email, "password": self._current_password}]},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 200:
            raise RuntimeError(f"邮箱创建失败: {result.get('message', result)}")
        logger.info(f"临时邮箱已创建: {email}")
        return email

    def _fetch_emails(self, email: str):
        """通过 Cloud Mail API 获取指定邮箱的邮件列表"""
        self._ensure_token()
        headers = {"Authorization": self.api_token, "Content-Type": "application/json"}
        resp = self.session.post(
            f"{self.worker_domain}/api/public/emailList",
            json={
                "toEmail": email,
                "type": 0,       # 收件
                "num": 1,        # 第1页
                "size": 10,      # 最多10封
                "timeSort": "desc",
            },
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 200:
                return result.get("data", [])
        return []

    @staticmethod
    def _extract_otp(content: str) -> str | None:
        """从邮件内容中提取 OTP"""
        patterns = [r"代码为\s*(\d{6})", r"code is\s*(\d{6})", r"(\d{6})"]
        for pattern in patterns:
            matches = re.findall(pattern, content)
            if matches:
                return matches[0]
        return None

    def wait_for_otp(self, email: str, timeout: int = 120) -> str:
        """阻塞等待 OTP 验证码"""
        logger.info(f"等待 OTP 验证码 (最长 {timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            emails = self._fetch_emails(email)
            for item in emails:
                sender = (item.get("sendEmail") or "").lower()
                # 优先使用纯文本，其次 HTML 内容
                raw = item.get("text") or item.get("content") or ""
                if "openai" in sender or "openai" in raw.lower():
                    otp = self._extract_otp(raw)
                    if otp:
                        logger.info(f"收到 OTP: {otp}")
                        return otp
            time.sleep(3)
        raise TimeoutError(f"等待 OTP 超时 ({timeout}s)")
