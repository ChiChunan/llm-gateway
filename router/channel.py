import re
from datetime import datetime, timedelta, timezone
from typing import Optional

CST = timezone(timedelta(hours=8))


class ChannelManager:
    def __init__(self, channels: list[str]):
        # 初始化所有渠道状态为可用（None 表示无封禁）
        self._status: dict[str, Optional[datetime]] = {c: None for c in channels}

    def is_available(self, channel: str) -> bool:
        """检查渠道是否可用，过期封禁自动解除"""
        until = self._status.get(channel)
        if until is None:
            return True
        if datetime.now(CST) >= until:
            self._status[channel] = None
            return True
        return False

    def mark_unavailable(self, channel: str, until: datetime):
        """将渠道标记为不可用，直到指定时间"""
        self._status[channel] = until

    def available_channels(self) -> list[str]:
        """返回当前所有可用渠道列表"""
        return [c for c in self._status if self.is_available(c)]

    def earliest_recovery(self) -> Optional[datetime]:
        """返回最早恢复时间，全部可用时返回 None"""
        times = [t for t in self._status.values() if t is not None]
        return min(times) if times else None

    def parse_reset_time(self, message: str) -> Optional[datetime]:
        """从 429 错误消息中解析重置时间，失败返回 None"""
        match = re.search(r'reset at (.+?)\.', message)
        if not match:
            return None
        raw = match.group(1).strip()
        # 去除末尾时区名称标签（如 " CST"），保留 +0800 偏移量
        raw = re.sub(r'\s+[A-Z]{2,4}$', '', raw)
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def handle_429(self, channel: str, error_message: str):
        """处理 429 限流：解析重置时间，无法解析则默认封禁 5 小时"""
        reset = self.parse_reset_time(error_message)
        if reset:
            self.mark_unavailable(channel, until=reset)
        else:
            self.mark_unavailable(channel, until=datetime.now(CST) + timedelta(hours=5))
