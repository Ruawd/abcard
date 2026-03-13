"""
HTTP 客户端 - 使用 curl_cffi 实现 TLS 指纹模拟
支持 Cloudflare 绕过，降级到 requests
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试使用 curl_cffi（推荐，自带 TLS 指纹模拟）
try:
    from curl_cffi.requests import Session as CffiSession

    _HAS_CFFI = True
    logger.debug("curl_cffi 可用，使用 TLS 指纹模拟")
except ImportError:
    _HAS_CFFI = False
    logger.debug("curl_cffi 不可用，降级到 requests")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 通用 UA
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def _build_requests_session(proxy: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if proxy:
        session.proxies = {"https": proxy, "http": proxy}
    session.headers["User-Agent"] = USER_AGENT
    return session


class HybridSession:
    """优先使用 curl_cffi，TLS 握手异常时自动降级到 requests。"""

    def __init__(self, cffi_session, requests_session):
        self._cffi = cffi_session
        self._requests = requests_session
        self._active = cffi_session
        self._use_requests = False

    @property
    def cookies(self):
        return self._active.cookies

    @property
    def headers(self):
        return self._active.headers

    @property
    def proxies(self):
        return self._active.proxies

    @proxies.setter
    def proxies(self, value):
        self._cffi.proxies = value
        self._requests.proxies = value

    def _is_tls_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        markers = [
            "curl: (35)",
            "tls connect error",
            "sslerror",
            "openssl_internal",
            "wrong version number",
            "sslv3 alert",
        ]
        return any(m in msg for m in markers)

    def _switch_to_requests(self):
        if self._use_requests:
            return
        logger.warning("检测到 TLS 异常，自动降级到 requests 会话")
        try:
            self._requests.cookies.update(self._cffi.cookies)
        except Exception:
            pass
        self._active = self._requests
        self._use_requests = True

    def request(self, method, url, **kwargs):
        if self._use_requests:
            return self._requests.request(method, url, **kwargs)
        try:
            return self._cffi.request(method, url, **kwargs)
        except Exception as e:
            if self._is_tls_error(e):
                self._switch_to_requests()
                return self._requests.request(method, url, **kwargs)
            raise

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def __getattr__(self, item):
        return getattr(self._active, item)


def create_http_session(proxy: Optional[str] = None, impersonate: str = "chrome136"):
    """
    创建 HTTP 会话。优先使用 curl_cffi 模拟浏览器 TLS 指纹，
    不可用时降级到 requests。
    """
    if _HAS_CFFI:
        cffi_session = CffiSession(impersonate=impersonate)
        if proxy:
            cffi_session.proxies = {"https": proxy, "http": proxy}
        req_session = _build_requests_session(proxy)
        return HybridSession(cffi_session, req_session)

    return _build_requests_session(proxy)
