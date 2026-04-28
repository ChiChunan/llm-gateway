"""跨模块共享的 channel_mgr 访问点，避免循环导入。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from channel import ChannelManager

_channel_mgr: "ChannelManager | None" = None
_config_path: str = "routing.yaml"


def set_channel_mgr(mgr: "ChannelManager", config_path: str = "routing.yaml"):
    global _channel_mgr, _config_path
    _channel_mgr = mgr
    _config_path = config_path


def get_channel_mgr() -> "ChannelManager":
    if _channel_mgr is None:
        raise RuntimeError("channel_mgr not initialized")
    return _channel_mgr


def get_config_path() -> str:
    return _config_path
