import re
from datetime import datetime, timedelta, timezone
from typing import Optional

CST = timezone(timedelta(hours=8))


class ChannelManager:
    def __init__(self, channels: list[str], plans: dict | None = None):
        # 所有模型的 429 封禁状态（None = 无封禁）
        self._status: dict[str, Optional[datetime]] = {c: None for c in channels}
        # plan 名称 -> 成员列表
        self._plans: dict[str, list[str]] = {}
        # model -> 所属 plan 名称
        self._channel_to_plan: dict[str, str] = {}
        # model 级别手动开关（与 429 封禁无关）
        self._model_enabled: dict[str, bool] = {}
        if plans:
            for plan_name, plan_cfg in plans.items():
                members = plan_cfg.get("channels", [])
                self._plans[plan_name] = members
                for ch in members:
                    self._channel_to_plan[ch] = plan_name

    def is_available(self, channel: str) -> bool:
        """检查模型是否可用（无 429 封禁）。"""
        until = self._status.get(channel)
        if until is None:
            return True
        if datetime.now(CST) >= until:
            self._status[channel] = None
            return True
        return False

    def mark_unavailable(self, channel: str, until: datetime):
        """将模型标记为不可用，直到指定时间。"""
        self._status[channel] = until

    def mark_available(self, channel: str):
        """立即恢复模型为可用状态。"""
        if channel in self._status:
            self._status[channel] = None

    def available_channels(self) -> list[str]:
        """返回当前所有可用模型列表（无封禁）。"""
        return [c for c in self._status if self.is_available(c)]

    def set_model_enabled(self, model: str, enabled: bool):
        """设置单个 model 的手动开关状态。"""
        self._model_enabled[model] = enabled

    def is_model_enabled(self, model: str) -> bool:
        """检查 model 是否被手动开启（默认 True）。"""
        return self._model_enabled.get(model, True)

    def earliest_recovery(self) -> Optional[datetime]:
        """返回最早恢复时间，全部可用时返回 None。"""
        now = datetime.now(CST)
        times = [t for t in self._status.values() if t is not None and t > now]
        return min(times) if times else None

    def parse_reset_time(self, message: str) -> Optional[datetime]:
        """从 429 错误消息中解析重置时间，失败返回 None。
        支持两种格式：
        - 火山引擎/字节跳动: reset at 2026-04-29 12:00:00+0800 CST.
        - DeepSeek: Rate limit exceeded, retry in X seconds/minutes.
        """
        # 火山引擎格式: reset at xxx.
        match = re.search(r'reset at (.+?)\.', message)
        if match:
            raw = match.group(1).strip()
            raw = re.sub(r'\s+CST$', '', raw)
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CST)
                return dt
            except ValueError:
                return None

        # DeepSeek 格式: retry in X seconds/minutes/hours
        match = re.search(r'retry in (\d+)\s*(second|minute|hour)s?', message, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            if unit == 'second':
                delta = timedelta(seconds=value)
            elif unit == 'minute':
                delta = timedelta(minutes=value)
            else:
                delta = timedelta(hours=value)
            return datetime.now(CST) + delta

        return None

    def handle_429(self, channel: str, error_message: str):
        """处理 429 限流：解析重置时间，无法解析或已过期则默认封禁 5 分钟。
        不再使用 plan 联动，每个模型独立管理。
        """
        # guard：channel 不存在时直接返回
        if channel not in self._status:
            return
        reset = self.parse_reset_time(error_message)
        now = datetime.now(CST)
        if reset and reset > now:
            until = reset
        else:
            until = now + timedelta(minutes=5)
        self.mark_unavailable(channel, until=until)
