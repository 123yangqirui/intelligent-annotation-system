"""登录失败限流。Redis 不可用时开发环境降级为进程内限流。"""

from hashlib import sha256
import time
from threading import Lock
from redis import Redis
from .config import settings
from .security import fail

fallback = {}
lock = Lock()


def login_limit(identity, failed=False):
    key = "login-failed:" + sha256(identity.encode()).hexdigest()
    client = Redis.from_url(
        settings().redis_cache_url, socket_timeout=1, socket_connect_timeout=1
    )
    try:
        count = int(client.get(key) or 0)
        if failed:
            count = client.incr(key)
            client.expire(key, 60)
    except Exception:
        if settings().app_env == "production":
            fail("AUTH_LIMIT_UNAVAILABLE", "认证限流暂不可用，请稍后重试。", 503)
        with lock:
            timestamp, count = fallback.get(key, (time.monotonic(), 0))
            if time.monotonic() - timestamp > 60:
                timestamp, count = time.monotonic(), 0
            if failed:
                count += 1
            fallback[key] = (timestamp, count)
            if len(fallback) > 10000:
                fallback.clear()
    if count >= 10:
        fail("RATE_LIMIT", "失败尝试过多，请一分钟后再试。", 429)
