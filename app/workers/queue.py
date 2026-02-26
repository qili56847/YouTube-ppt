"""SSE 事件发布/订阅管理器。"""

import asyncio
from typing import AsyncGenerator
from collections import defaultdict
from loguru import logger


class EventQueue:
    """管理每个任务的 SSE 事件队列，支持多订阅者。"""

    def __init__(self):
        """初始化事件队列管理器。"""
        self._queues: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, job_id: int) -> asyncio.Queue:
        """为指定任务创建并注册一个事件队列。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues[job_id].append(q)
        logger.debug(f"新订阅者注册 job_id={job_id}, 当前订阅数={len(self._queues[job_id])}")
        return q

    def unsubscribe(self, job_id: int, q: asyncio.Queue) -> None:
        """取消订阅，从队列列表中移除。"""
        if job_id in self._queues and q in self._queues[job_id]:
            self._queues[job_id].remove(q)
            if not self._queues[job_id]:
                del self._queues[job_id]
            logger.debug(f"订阅者取消注册 job_id={job_id}")

    async def publish(self, job_id: int, event: dict) -> None:
        """向所有订阅者发布事件。"""
        if job_id not in self._queues:
            return
        for q in list(self._queues[job_id]):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"事件队列已满，跳过 job_id={job_id}")

    async def stream(self, job_id: int) -> AsyncGenerator[dict, None]:
        """生成器：持续产出指定任务的 SSE 事件，直到收到终止信号。每 30s 发送心跳保持连接。"""
        q = self.subscribe(job_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield event
                    # 终止信号
                    if event.get("status") in ("completed", "failed"):
                        break
                except asyncio.TimeoutError:
                    # 发送心跳保持连接，继续等待下一个事件
                    yield {"type": "ping"}
        except Exception as e:
            logger.error(f"SSE 流异常 job_id={job_id}: {e}")
        finally:
            self.unsubscribe(job_id, q)


# 全局事件队列实例
event_queue = EventQueue()
