"""图片优化服务：使用 Pillow 压缩帧图片并转换为 Base64。"""

import base64
import io
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow 未安装，图片优化功能不可用")


class ImageOptimizer:
    """图片压缩与 Base64 编码工具。"""

    # 输出图片最大尺寸（宽 x 高）
    MAX_WIDTH = 1280
    MAX_HEIGHT = 720

    def __init__(self, quality: int = 75):
        """
        初始化图片优化器。

        参数：
            quality: JPEG 压缩质量（1-95，越高质量越好文件越大）
        """
        self.quality = max(1, min(95, quality))

    def thumbnail_to_base64(self, image_path: Path, thumb_quality: int = 25) -> str:
        """
        生成低质量缩略图 Base64，用于侧边栏预览（尺寸小，加载快）。

        参数：
            image_path: 图片文件路径
            thumb_quality: 缩略图 JPEG 质量（默认 25）

        返回：
            形如 'data:image/jpeg;base64,xxxx' 的字符串
        """
        if not image_path.exists() or image_path.stat().st_size == 0:
            return self._placeholder_base64()

        if not PIL_AVAILABLE:
            return self._placeholder_base64()

        try:
            with Image.open(image_path) as img:
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                img.thumbnail((240, 135), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=thumb_quality)
                buf.seek(0)
                encoded = base64.b64encode(buf.read()).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception as e:
            logger.error(f"缩略图生成失败 {image_path}: {e}")
            return self._placeholder_base64()

    def optimize_to_base64(self, image_path: Path) -> str:
        """
        读取图片文件，压缩优化后转为 Base64 Data URL。

        参数：
            image_path: 图片文件路径

        返回：
            形如 'data:image/jpeg;base64,xxxx' 的字符串
        """
        if not image_path.exists() or image_path.stat().st_size == 0:
            return self._placeholder_base64()

        if not PIL_AVAILABLE:
            return self._file_to_base64(image_path)

        try:
            with Image.open(image_path) as img:
                # 转为 RGB（去除 RGBA 透明通道）
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")

                # 等比缩放
                img.thumbnail((self.MAX_WIDTH, self.MAX_HEIGHT), Image.LANCZOS)

                # 编码为 JPEG bytes
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self.quality)
                buf.seek(0)
                encoded = base64.b64encode(buf.read()).decode("utf-8")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception as e:
            logger.error(f"图片优化失败 {image_path}: {e}")
            return self._placeholder_base64()

    @staticmethod
    def _file_to_base64(image_path: Path) -> str:
        """直接将文件转为 Base64（Pillow 不可用时的备选）。"""
        data = image_path.read_bytes()
        encoded = base64.b64encode(data).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
    def _placeholder_base64() -> str:
        """返回 1x1 灰色占位图的 Base64。"""
        # 1x1 灰色 JPEG 的 Base64
        PLACEHOLDER = (
            "data:image/jpeg;base64,"
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
            "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
            "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAAAAAFBv/EABQQAQAA"
            "AAAAAAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAA"
            "AAAA/9oADAMBAAIRAxEAPwCwABmX/9k="
        )
        return PLACEHOLDER
