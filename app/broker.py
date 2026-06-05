"""进程内 pub/sub 广播（KTD6）。

注册表 dict[poll_id, set[asyncio.Queue]]。投票（在 threadpool 的同步路由里）
调用 publish 发信号；每个 SSE 连接收到信号后按自己的 voter_key 重渲染结果
——因此可见性（已投/未投/隐藏）逐连接独立正确。

publish 可能从 threadpool 线程调用，而 asyncio.Queue.put_nowait 非线程安全，
故经 loop.call_soon_threadsafe 投递。多 worker/多机时换 Redis pub/sub，
接口（subscribe/unsubscribe/publish）不变（KTD6 / Risk R-3）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, poll_id: str) -> asyncio.Queue:
        # subscribe 在 SSE async 端点内调用，捕获运行中的事件循环供跨线程投递。
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[poll_id].add(queue)
        return queue

    def unsubscribe(self, poll_id: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(poll_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(poll_id, None)  # 防注册表泄漏

    def publish(self, poll_id: str) -> None:
        """向该 poll 的所有订阅者发"已更新"信号。可从任意线程调用。"""
        subs = self._subs.get(poll_id)
        if not subs:
            return
        loop = self._loop
        for queue in list(subs):
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(queue.put_nowait, None)
            else:
                queue.put_nowait(None)

    def subscriber_count(self, poll_id: str) -> int:
        return len(self._subs.get(poll_id, ()))


broker = Broker()
