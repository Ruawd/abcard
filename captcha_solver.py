"""
hCaptcha 打码服务 - 通过 YesCaptcha API 解决 Stripe intent_confirmation_challenge
"""
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """YesCaptcha hCaptcha 打码"""

    def __init__(self, api_url: str, client_key: str):
        self.api_url = api_url.rstrip("/")
        self.client_key = client_key

    def solve_hcaptcha(
        self,
        site_key: str,
        site_url: str,
        rqdata: str = "",
        timeout: int = 120,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        提交 hCaptcha 任务并等待结果。
        返回 gRecaptchaResponse token，失败返回 None。
        """
        task = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": site_url,
            "websiteKey": site_key,
        }
        if rqdata:
            task["enterprisePayload"] = {"rqdata": rqdata}
            task["isEnterprise"] = True

        create_body = {
            "clientKey": self.client_key,
            "task": task,
        }

        logger.info(f"提交 hCaptcha 任务: site_key={site_key[:20]}...")
        try:
            resp = requests.post(
                f"{self.api_url}/createTask",
                json=create_body,
                timeout=30,
            )
            data = resp.json()
        except Exception as e:
            logger.error(f"创建打码任务失败: {e}")
            return None

        if data.get("errorId", 0) != 0:
            logger.error(f"打码任务创建失败: {data.get('errorDescription', data)}")
            return None

        task_id = data.get("taskId")
        if not task_id:
            logger.error(f"未返回 taskId: {data}")
            return None

        logger.info(f"打码任务已创建: taskId={task_id}")

        # 轮询结果
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                result_resp = requests.post(
                    f"{self.api_url}/getTaskResult",
                    json={"clientKey": self.client_key, "taskId": task_id},
                    timeout=30,
                )
                result_data = result_resp.json()
            except Exception as e:
                logger.warning(f"查询打码结果异常: {e}")
                continue

            status = result_data.get("status", "")
            if status == "ready":
                token = result_data.get("solution", {}).get("gRecaptchaResponse", "")
                if token:
                    logger.info(f"hCaptcha 已解决, token 长度: {len(token)}")
                    return token
                logger.error(f"打码结果缺少 token: {result_data}")
                return None
            elif status == "processing":
                logger.debug(f"打码中... (已等待 {int(time.time() - (deadline - timeout))}s)")
            else:
                logger.error(f"打码失败: {result_data}")
                return None

        logger.error(f"打码超时 ({timeout}s)")
        return None
