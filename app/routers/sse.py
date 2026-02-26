"""SSE 实时进度推送路由。"""

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from app.workers.queue import event_queue

router = APIRouter(prefix="/api/events", tags=["sse"])


@router.get("/{job_id}")
async def job_events(job_id: int, request: Request) -> StreamingResponse:
    """
    SSE 端点：持续推送任务进度事件，直到任务完成或客户端断开。

    客户端使用 EventSource('GET /api/events/{job_id}') 监听。
    """
    return StreamingResponse(
        _event_generator(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_generator(job_id: int, request: Request) -> AsyncGenerator[str, None]:
    """生成 SSE 事件流，监听客户端断开连接。"""
    logger.info(f"SSE 连接建立 job_id={job_id}")

    # 发送初始连接确认
    yield _format_sse({"type": "connected", "job_id": job_id})

    try:
        async for event in event_queue.stream(job_id):
            # 检查客户端是否已断开
            if await request.is_disconnected():
                logger.info(f"SSE 客户端断开 job_id={job_id}")
                break

            yield _format_sse(event)

            # 任务完成或失败时结束流
            if event.get("status") in ("completed", "failed"):
                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"SSE 流异常 job_id={job_id}: {e}")
        yield _format_sse({"type": "error", "message": str(e)})
    finally:
        logger.info(f"SSE 连接关闭 job_id={job_id}")


def _format_sse(data: dict) -> str:
    """将字典格式化为 SSE 数据帧字符串。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
