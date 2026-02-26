"""投影片生成服务：将帧图片与字幕组合为自包含 HTML 文件。"""

import json
from pathlib import Path
from typing import Optional
from loguru import logger

from app.services.subtitle import SubtitleSegment


class SlideBuilder:
    """生成自包含 HTML 投影片，支持键盘导航与缩略图。"""

    def __init__(self, output_dir: Path):
        """
        初始化投影片生成器。

        参数：
            output_dir: 输出目录（HTML 将写入此目录）
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        segments: list[SubtitleSegment],
        frame_base64_list: list[str],
        metadata: dict,
        thumb_base64_list: Optional[list[str]] = None,
        outline: Optional[str] = None,
    ) -> Path:
        """
        生成自包含 HTML 投影片文件。

        参数：
            segments: 字幕片段列表
            frame_base64_list: 每个片段对应的帧 Base64 Data URL 列表（主图，高质量）
            metadata: 影片元数据（title, duration, thumbnail 等）
            thumb_base64_list: 侧边栏缩略图 Base64 列表（低质量，留空则复用主图）

        返回：
            生成的 HTML 文件路径
        """
        slides = []
        for idx, (seg, img_data) in enumerate(zip(segments, frame_base64_list)):
            thumb = thumb_base64_list[idx] if thumb_base64_list and idx < len(thumb_base64_list) else img_data
            slide = {
                "id": idx,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "translation": seg.translation,
                "image": img_data,
                "thumb": thumb,
                "timestamp": self._format_time(seg.start),
            }
            slides.append(slide)

        title = metadata.get("title", "YouTube 投影片")
        try:
            import opencc
            title = opencc.OpenCC("t2s").convert(title)
        except Exception:
            pass
        html = self._render_html(title, slides, metadata, outline)

        output_path = self.output_dir / "slides.html"
        output_path.write_text(html, encoding="utf-8")
        logger.info(f"投影片生成完成: {output_path} ({len(slides)} 张)")
        return output_path

    def _render_html(self, title: str, slides: list[dict], metadata: dict, outline: Optional[str] = None) -> str:
        """渲染完整的 HTML 字符串。"""
        from app.services.ai_outline import _markdown_to_html
        slides_json = json.dumps(slides, ensure_ascii=False)
        meta_json = json.dumps(metadata, ensure_ascii=False)
        slide_count = len(slides)
        duration_str = self._format_time(metadata.get("duration", 0))

        # 预渲染第一张投影片，确保页面打开立即可见（不依赖 JS 执行）
        first = slides[0] if slides else {}
        first_img = first.get("image", "")
        first_text = self._escape_html(first.get("text", ""))
        first_ts = first.get("timestamp", "")
        first_trans = first.get("translation") or ""
        first_trans_html = self._escape_html(first_trans)
        first_trans_style = "" if first_trans else ' style="display:none"'

        return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self._escape_html(title)}</title>
<style>
  :root {{
    --bg: #0f1117;
    --surface: #1a1d2e;
    --surface2: #252840;
    --accent: #6c63ff;
    --accent2: #ff6584;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --border: #2d3354;
    --radius: 12px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  /* 顶部导航 */
  .navbar {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .navbar h1 {{
    font-size: 1rem;
    font-weight: 600;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .navbar .meta {{
    font-size: 0.8rem;
    color: var(--text-muted);
    white-space: nowrap;
  }}
  .btn {{
    padding: 6px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    cursor: pointer;
    font-size: 0.85rem;
    transition: all 0.2s;
    white-space: nowrap;
  }}
  .btn:hover {{ background: var(--accent); border-color: var(--accent); }}
  .btn.active {{ background: var(--accent); border-color: var(--accent); }}

  /* 主区域：左侧缩略图 + 右侧主内容 */
  .main-layout {{
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }}

  /* 缩略图侧边栏 */
  .sidebar {{
    width: 200px;
    min-width: 200px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .sidebar::-webkit-scrollbar {{ width: 4px; }}
  .sidebar::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}
  .thumb-item {{
    cursor: pointer;
    border-radius: 8px;
    clip-path: inset(0 round 8px);
    border: 2px solid transparent;
    transition: border-color 0.2s;
    position: relative;
  }}
  .thumb-item:hover {{ border-color: var(--accent2); }}
  .thumb-item.active {{ border-color: var(--accent); }}
  .thumb-item img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }}
  .thumb-item .thumb-num {{
    position: absolute;
    top: 4px;
    left: 4px;
    background: rgba(0,0,0,0.7);
    color: #fff;
    font-size: 0.65rem;
    padding: 1px 5px;
    border-radius: 4px;
  }}
  .thumb-item .thumb-text {{
    padding: 4px 6px;
    font-size: 0.7rem;
    color: var(--text-muted);
    line-height: 1.3;
    max-height: 3.2em;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}

  /* 内容区 */
  .content {{
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  /* 单张投影片模式 */
  .slide-view {{
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    padding: 24px;
    overflow-y: auto;
  }}
  .slide-container {{
    max-width: 900px;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }}
  .slide-img-wrap {{
    position: relative;
    width: 100%;
    aspect-ratio: 16/9;
    border-radius: var(--radius);
    overflow: hidden;
    background: #000;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }}
  .slide-img {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}
  .slide-timestamp {{
    position: absolute;
    bottom: 8px;
    right: 8px;
    background: rgba(0,0,0,0.75);
    color: #fff;
    font-size: 0.75rem;
    padding: 2px 8px;
    border-radius: 4px;
    font-family: monospace;
  }}
  .slide-text-wrap {{
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px 20px;
    border: 1px solid var(--border);
  }}
  .slide-text {{
    font-size: 1.05rem;
    line-height: 1.7;
    color: var(--text);
  }}
  .slide-translation {{
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text-muted);
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }}

  /* 导航控制 */
  .nav-controls {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 16px;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }}
  .nav-btn {{
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    cursor: pointer;
    font-size: 1.2rem;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
  }}
  .nav-btn:hover:not(:disabled) {{ background: var(--accent); border-color: var(--accent); }}
  .nav-btn:disabled {{ opacity: 0.3; cursor: not-allowed; }}
  .nav-counter {{
    font-size: 0.9rem;
    color: var(--text-muted);
    min-width: 80px;
    text-align: center;
  }}

  /* 全部模式 */
  .all-view {{
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: none;
    flex-direction: column;
    gap: 24px;
  }}
  .all-view::-webkit-scrollbar {{ width: 6px; }}
  .all-view::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
  .all-view.visible {{ display: flex; }}
  .all-slide-item {{
    display: flex;
    gap: 16px;
    background: var(--surface);
    border-radius: var(--radius);
    padding: 16px;
    border: 1px solid var(--border);
    cursor: pointer;
    transition: border-color 0.2s;
  }}
  .all-slide-item:hover {{ border-color: var(--accent); }}
  .all-slide-img {{ width: 240px; min-width: 240px; border-radius: 8px; object-fit: cover; aspect-ratio: 16/9; }}
  .all-slide-info {{ flex: 1; }}
  .all-slide-num {{ font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; }}
  .all-slide-text {{ font-size: 0.95rem; line-height: 1.6; }}
  .all-slide-trans {{ font-size: 0.85rem; color: var(--text-muted); margin-top: 6px; }}

  /* 搜索 */
  .search-wrap {{
    padding: 8px 24px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: none;
  }}
  .search-wrap.visible {{ display: block; }}
  .search-input {{
    width: 100%;
    padding: 8px 14px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    font-size: 0.9rem;
    outline: none;
  }}
  .search-input:focus {{ border-color: var(--accent); }}

  /* 进度条 */
  .progress-bar {{
    height: 2px;
    background: var(--border);
    position: relative;
  }}
  .progress-fill {{
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    transition: width 0.3s;
  }}

  @media (max-width: 768px) {{
    .sidebar {{ display: none; }}
    .all-slide-img {{ width: 120px; min-width: 120px; }}
  }}

  /* 大纲模态框 */
  .outline-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    z-index: 200;
  }}
  .outline-overlay.visible {{ display: block; }}
  .outline-panel {{
    position: fixed;
    top: 0;
    right: -480px;
    width: 460px;
    max-width: 90vw;
    height: 100%;
    background: var(--surface);
    border-left: 1px solid var(--border);
    z-index: 201;
    display: flex;
    flex-direction: column;
    transition: right 0.3s ease;
    box-shadow: -8px 0 32px rgba(0,0,0,0.4);
  }}
  .outline-panel.visible {{ right: 0; }}
  .outline-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    font-weight: 600;
    font-size: 1rem;
    flex-shrink: 0;
  }}
  .outline-close {{
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    width: 28px;
    height: 28px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.9rem;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .outline-close:hover {{ background: var(--surface2); }}
  .outline-content {{
    padding: 20px;
    overflow-y: auto;
    flex: 1;
    line-height: 1.7;
    font-size: 0.95rem;
  }}
  .outline-content h3 {{
    color: var(--accent);
    font-size: 1rem;
    margin: 20px 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }}
  .outline-content h3:first-child {{ margin-top: 0; }}
  .outline-content h4 {{
    font-size: 0.9rem;
    margin: 14px 0 6px;
    color: var(--text);
  }}
  .outline-content p {{
    color: var(--text-muted);
    margin: 6px 0;
  }}
  .outline-content ul {{
    margin: 6px 0 12px;
    padding-left: 20px;
  }}
  .outline-content li {{
    color: var(--text-muted);
    margin: 4px 0;
  }}
  .outline-content strong {{ color: var(--text); }}
</style>
</head>
<body>

<nav class="navbar">
  <h1 id="pageTitle">{self._escape_html(title)}</h1>
  <span class="meta" id="metaInfo">{slide_count} 张 · {duration_str}</span>
  <button class="btn" id="searchBtn" onclick="toggleSearch()">🔍 搜索</button>
  <button class="btn" id="modeBtn" onclick="toggleMode()">📋 全部</button>
  {'<button class="btn" id="outlineBtn" onclick="toggleOutline()">📑 大纲</button>' if outline else ''}
</nav>

<div class="progress-bar">
  <div class="progress-fill" id="progressFill" style="width: 0%"></div>
</div>

<div class="search-wrap" id="searchWrap">
  <input class="search-input" id="searchInput" placeholder="搜索字幕内容..." oninput="filterSlides(this.value)">
</div>

<div class="main-layout">
  <!-- 缩略图侧边栏 -->
  <div class="sidebar" id="sidebar"></div>

  <!-- 内容区 -->
  <div class="content">
    <!-- 单张模式 -->
    <div class="slide-view" id="slideView">
      <div class="slide-container">
        <div class="slide-img-wrap">
          <img class="slide-img" id="slideImg" src="{first_img}" alt="">
          <span class="slide-timestamp" id="slideTimestamp">{first_ts}</span>
        </div>
        <div class="slide-text-wrap">
          <div class="slide-text" id="slideText">{first_text}</div>
          <div class="slide-translation" id="slideTranslation"{first_trans_style}>{first_trans_html}</div>
        </div>
      </div>
    </div>

    <!-- 全部模式 -->
    <div class="all-view" id="allView"></div>

    <!-- 导航控制 -->
    <div class="nav-controls" id="navControls">
      <button class="nav-btn" id="firstBtn" onclick="goTo(0)" title="第一张">⏮</button>
      <button class="nav-btn" id="prevBtn" onclick="prev()" title="上一张 (←)">◀</button>
      <span class="nav-counter" id="navCounter">1 / {slide_count}</span>
      <button class="nav-btn" id="nextBtn" onclick="next()" title="下一张 (→)">▶</button>
      <button class="nav-btn" id="lastBtn" onclick="goTo(slides.length-1)" title="最后一张">⏭</button>
    </div>
  </div>
</div>

<script>
const slides = {slides_json};
const metadata = {meta_json};
let current = 0;
let isAllMode = false;
let allViewBuilt = false;
let filteredIndices = slides.map((_, i) => i);

// 初始化
function init() {{
  // goTo(0) 立即同步执行（激活首张投影片的导航状态）
  goTo(0);
  updateProgress();
  // 侧边栏延迟到下一帧，避免阻塞首屏渲染
  requestAnimationFrame(() => {{
    buildSidebar();
    document.getElementById('thumb-0')?.classList.add('active');
  }});
}}

function buildSidebar() {{
  const sb = document.getElementById('sidebar');
  const BATCH = 20;  // 每批渲染数量

  function renderBatch(start) {{
    if (start >= slides.length) return;
    const frag = document.createDocumentFragment();
    const end = Math.min(start + BATCH, slides.length);
    for (let i = start; i < end; i++) {{
      const slide = slides[i];
      const div = document.createElement('div');
      div.className = 'thumb-item';
      div.id = 'thumb-' + i;
      div.onclick = () => {{ goTo(i); if (isAllMode) toggleMode(); }};

      const img = document.createElement('img');
      img.src = slide.thumb || slide.image;
      img.alt = '';

      const numSpan = document.createElement('span');
      numSpan.className = 'thumb-num';
      numSpan.textContent = i + 1;

      const textDiv = document.createElement('div');
      textDiv.className = 'thumb-text';
      textDiv.textContent = slide.text;

      div.appendChild(img);
      div.appendChild(numSpan);
      div.appendChild(textDiv);
      frag.appendChild(div);
    }}
    sb.appendChild(frag);
    if (end < slides.length) {{
      setTimeout(() => renderBatch(end), 0);
    }}
  }}

  renderBatch(0);
}}

function buildAllView() {{
  const view = document.getElementById('allView');
  const observer = new IntersectionObserver((entries) => {{
    entries.forEach(entry => {{
      if (entry.isIntersecting) {{
        const img = entry.target;
        if (!img.dataset.loaded) {{
          img.src = slides[+img.dataset.idx].image;
          img.dataset.loaded = '1';
          observer.unobserve(img);
        }}
      }}
    }});
  }}, {{ rootMargin: '300px' }});

  const frag = document.createDocumentFragment();
  slides.forEach((slide, i) => {{
    const div = document.createElement('div');
    div.className = 'all-slide-item';
    div.id = 'all-' + i;
    div.onclick = () => {{ goTo(i); toggleMode(); }};

    const img = document.createElement('img');
    img.className = 'all-slide-img';
    img.dataset.idx = i;
    img.alt = '';
    observer.observe(img);

    const info = document.createElement('div');
    info.className = 'all-slide-info';

    const num = document.createElement('div');
    num.className = 'all-slide-num';
    num.textContent = '#' + (i + 1) + '  ' + slide.timestamp;

    const text = document.createElement('div');
    text.className = 'all-slide-text';
    text.textContent = slide.text;

    info.appendChild(num);
    info.appendChild(text);

    if (slide.translation) {{
      const trans = document.createElement('div');
      trans.className = 'all-slide-trans';
      trans.textContent = slide.translation;
      info.appendChild(trans);
    }}

    div.appendChild(img);
    div.appendChild(info);
    frag.appendChild(div);
  }});
  view.appendChild(frag);
}}

function goTo(idx) {{
  if (idx < 0 || idx >= slides.length) return;
  // 去活当前缩略图
  document.getElementById(`thumb-${{current}}`)?.classList.remove('active');
  current = idx;
  const slide = slides[current];

  // 更新主视图
  document.getElementById('slideImg').src = slide.image;
  document.getElementById('slideTimestamp').textContent = slide.timestamp;
  document.getElementById('slideText').textContent = slide.text;

  const transEl = document.getElementById('slideTranslation');
  if (slide.translation) {{
    transEl.textContent = slide.translation;
    transEl.style.display = '';
  }} else {{
    transEl.style.display = 'none';
  }}

  // 更新缩略图活跃状态
  const thumb = document.getElementById(`thumb-${{current}}`);
  thumb?.classList.add('active');
  thumb?.scrollIntoView({{ block: 'nearest' }});

  // 更新导航
  document.getElementById('navCounter').textContent = `${{current + 1}} / ${{slides.length}}`;
  document.getElementById('prevBtn').disabled = current === 0;
  document.getElementById('firstBtn').disabled = current === 0;
  document.getElementById('nextBtn').disabled = current === slides.length - 1;
  document.getElementById('lastBtn').disabled = current === slides.length - 1;

  updateProgress();
}}

function prev() {{ goTo(current - 1); }}
function next() {{ goTo(current + 1); }}

function updateProgress() {{
  const pct = slides.length > 1 ? (current / (slides.length - 1)) * 100 : 100;
  document.getElementById('progressFill').style.width = pct + '%';
}}

function toggleMode() {{
  isAllMode = !isAllMode;
  if (isAllMode && !allViewBuilt) {{
    buildAllView();
    allViewBuilt = true;
  }}
  document.getElementById('slideView').style.display = isAllMode ? 'none' : '';
  document.getElementById('navControls').style.display = isAllMode ? 'none' : '';
  document.getElementById('allView').classList.toggle('visible', isAllMode);
  document.getElementById('modeBtn').textContent = isAllMode ? '▶ 单张' : '📋 全部';
  document.getElementById('modeBtn').classList.toggle('active', isAllMode);
}}

function toggleSearch() {{
  const wrap = document.getElementById('searchWrap');
  const visible = wrap.classList.toggle('visible');
  if (visible) {{
    document.getElementById('searchInput').focus();
  }} else {{
    document.getElementById('searchInput').value = '';
    filterSlides('');
  }}
}}

function filterSlides(query) {{
  const q = query.toLowerCase().trim();
  const allItems = document.querySelectorAll('.all-slide-item');
  const thumbItems = document.querySelectorAll('.thumb-item');

  if (!q) {{
    allItems.forEach(el => el.style.display = '');
    thumbItems.forEach(el => el.style.display = '');
    return;
  }}

  slides.forEach((slide, i) => {{
    const match = slide.text.toLowerCase().includes(q) ||
                  (slide.translation && slide.translation.toLowerCase().includes(q));
    const allEl = document.getElementById(`all-${{i}}`);
    const thumbEl = document.getElementById(`thumb-${{i}}`);
    if (allEl) allEl.style.display = match ? '' : 'none';
    if (thumbEl) thumbEl.style.display = match ? '' : 'none';
  }});
}}

function escHtml(str) {{
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}}

// 键盘导航
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT') return;
  switch(e.key) {{
    case 'ArrowLeft': case 'ArrowUp': prev(); break;
    case 'ArrowRight': case 'ArrowDown': next(); break;
    case 'Home': goTo(0); break;
    case 'End': goTo(slides.length - 1); break;
    case 'f': case 'F': toggleMode(); break;
    case '/': e.preventDefault(); toggleSearch(); break;
  }}
}});

init();
</script>

{f'''
<!-- 大纲模态框 -->
<div class="outline-overlay" id="outlineOverlay" onclick="toggleOutline()"></div>
<div class="outline-panel" id="outlinePanel">
  <div class="outline-header">
    <span>📑 视频大纲</span>
    <button class="outline-close" onclick="toggleOutline()">✕</button>
  </div>
  <div class="outline-content">{_markdown_to_html(outline)}</div>
</div>
<script>
function toggleOutline() {{
  document.getElementById('outlineOverlay').classList.toggle('visible');
  document.getElementById('outlinePanel').classList.toggle('visible');
}}
</script>
''' if outline else ''}
</body>
</html>"""

    @staticmethod
    def _format_time(seconds: float) -> str:
        """将秒数格式化为 HH:MM:SS 字符串。"""
        total = int(seconds)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _escape_html(text: str) -> str:
        """转义 HTML 特殊字符。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
