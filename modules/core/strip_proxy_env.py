"""
在进程启动早期移除 HTTP(S)/SOCKS 代理相关环境变量。

场景：开发者为 Docker pull 曾在终端 export HTTP_PROXY；子进程 Python 若继承，
则 httpx 访问 DashScope 会误走代理导致超时或证书问题。

Docker Desktop 的代理应在「Docker 设置」中单独配置，与终端环境变量解耦。
"""

from __future__ import annotations

import os  # 读写进程环境变量

_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)  # 常见大小写形式一并清除


def strip_http_proxy_environment() -> None:
    """对上述键逐个 pop，不存在则忽略。"""
    for k in _KEYS:
        os.environ.pop(k, None)  # 删除键；无该键时返回 None 不抛异常
