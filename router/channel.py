import re
from datetime import datetime, timedelta, timezone
from typing import Optional

CST = timezone(timedelta(hours=8))


class ChannelManager:
    def __init__(self, channels: list[str], plans: dict | None = None):
        # 初始化所有渠道状态为可用（None 表示无封禁）
        self._status: dict[str, Optional[datetime]] = {c: None for c in channels}
        # plan 名称 -> 成员渠道列表
        self._plans: dict[str, list[str]] = {}
        # 渠道 -> 所属 plan 名称（无 plan 时不存在该 key）
        self._channel_to_plan: dict[str, str] = {}
        if plans:
            for plan_name, plan_cfg in plans.items():
                members = plan_cfg.get("channels", [])
                self._plans[plan_name] = members
                for ch in members:
                    self._channel_to_plan[ch] = plan_name

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

    def mark_available(self, channel: str):
        """立即恢复渠道为可用状态"""
        if channel in self._status:
            self._status[channel] = None

    def available_channels(self) -> list[str]:
        """返回当前所有可用渠道列表"""
        return [c for c in self._status if self.is_available(c)]

    def earliest_recovery(self) -> Optional[datetime]:
        """返回最早恢复时间，全部可用时返回 None"""
        now = datetime.now(CST)
        times = [t for t in self._status.values() if t is not None and t > now]
        return min(times) if times else None

    def parse_reset_time(self, message: str) -> Optional[datetime]:
        """从 429 错误消息中解析重置时间，失败返回 None"""
        match = re.search(r'reset at (.+?)\.', message)
        if not match:
            return None
        raw = match.group(1).strip()
        # 去除末尾时区名称标签（如 " CST"），保留 +0800 偏移量
        raw = re.sub(r'\s+CST$', '', raw)
        try:
            dt = datetime.fromisoformat(raw)
            # 确保返回的 datetime 带时区信息，避免与 aware datetime 比较时抛 TypeError
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt
        except ValueError:
            return None

    def handle_429(self, channel: str, error_message: str):
        """处理 429 限流：解析重置时间，无法解析或已过期则默认封禁 5 小时。
        若渠道属于某 plan，则将 plan 内所有成员同步标记为同一解封时间。
        """
        # guard：channel 不存在时直接返回，避免静默创建新 key
        if channel not in self._status:
            return
        reset = self.parse_reset_time(error_message)
        now = datetime.now(CST)
        # reset 已是过去时间时，以当前时间为基准封禁 5 小时
        if reset and reset > now:
            until = reset
        else:
            until = now + timedelta(hours=5)
        self.mark_unavailable(channel, until=until)
        # plan 联动：同 plan 内其余成员使用相同解封时间
        plan_name = self._channel_to_plan.get(channel)
        if plan_name:
            for sibling in self._plans.get(plan_name, []):
                if sibling != channel and sibling in self._status:
                    self.mark_unavailable(sibling, until=until)
