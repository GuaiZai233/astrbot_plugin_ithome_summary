# main.py
import html
import re
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image
from astrbot.core.platform.astr_message_event import AstrMessageEvent

# 匹配 IT之家 新闻链接的两种主要形态
_PATTERNS = [
    # https://m.ithome.com/html/974846.htm
    re.compile(r"(?:https?://)?m\.ithome\.com/html/(\d+)\.htm", re.I),
    # https://www.ithome.com/0/974/846.htm  ->  974 + 846
    re.compile(r"(?:https?://)?(?:www\.)?ithome\.com/\d+/(\d+)/(\d+)\.htm", re.I),
]

TEMPLATE_PATH = Path(__file__).parent / "templates" / "card.html"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@register(
    "astrbot_plugin_ithome_summary",
    "frostfallx",
    "自动解析 IT之家 新闻链接并渲染为带 AI 总结的图片卡片",
    "v1.0.0",
    "https://github.com/frostfallx/astrbot_plugin_ithome_summary",
)
class IThomeSummaryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._template = ""
        # 去重记录： {umo::newsid: timestamp}
        self._recent: dict[str, float] = {}

    async def initialize(self):
        try:
            self._template = TEMPLATE_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"[ithome] 读取模板失败: {e}")
            self._template = ""

    # ---------- 工具方法 ----------
    @staticmethod
    def _extract_newsid(text: str) -> str | None:
        """从文本中提取第一个 IT之家 新闻链接，返回归一化的 newsid。"""
        for i, pat in enumerate(_PATTERNS):
            m = pat.search(text)
            if not m:
                continue
            if i == 0:
                return m.group(1)
            # 第二种形态: /0/974/846.htm -> 974846
            return m.group(1) + m.group(2)
        return None

    @staticmethod
    def _api_url(newsid: str) -> str:
        """newsid -> API url。974846 -> .../974/846.xml"""
        head, tail = newsid[:-3], newsid[-3:]
        return f"https://api.ithome.com/xml/newscontent/{head}/{tail}.xml"

    @staticmethod
    def _clean_html(raw: str) -> str:
        """把新闻正文 HTML 去格式化为纯文本，保留段落换行。"""
        if not raw:
            return ""
        # AI 延伸阅读等区块整体去掉
        raw = re.sub(r"<blockquote[^>]*>.*?</blockquote>", "", raw, flags=re.S | re.I)
        # 块级标签转换为换行
        raw = re.sub(r"</(p|div|h[1-6]|li|br)>", "\n", raw, flags=re.I)
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
        # 去掉剩余全部标签（含 img）
        raw = re.sub(r"<[^>]+>", "", raw)
        # 还原 HTML 实体
        raw = html.unescape(raw)
        # 压缩多余空白
        lines = [ln.strip() for ln in raw.splitlines()]
        lines = [ln for ln in lines if ln]
        return "\n".join(lines)

    @staticmethod
    def _first_img(detail_html: str) -> str:
        m = re.search(r'<img[^>]+src="([^"]+)"', detail_html or "", re.I)
        if not m:
            return ""
        src = m.group(1)
        # 去掉图片处理参数，避免异常
        return src.split("?")[0] if src.startswith("http") else src

    @staticmethod
    def _tag(xml: str, name: str) -> str:
        """从 XML 文本中取单个标签内容（兼容 CDATA）。"""
        m = re.search(
            rf"<{name}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{name}>",
            xml,
            flags=re.S | re.I,
        )
        return m.group(1).strip() if m else ""

    def _dedupe(self, umo: str, newsid: str) -> bool:
        """返回 True 表示应跳过（近期已解析过）。"""
        interval = int(self.config.get("dedupe_interval", 120) or 0)
        if interval <= 0:
            return False
        key = f"{umo}::{newsid}"
        now = time.time()
        # 顺手清理过期项
        expired = [k for k, t in self._recent.items() if now - t > interval]
        for k in expired:
            self._recent.pop(k, None)
        if key in self._recent and now - self._recent[key] < interval:
            return True
        self._recent[key] = now
        return False

    # ---------- 网络 ----------
    async def _fetch_xml(self, url: str) -> str | None:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url, headers={"User-Agent": _UA}) as resp:
                    if resp.status != 200:
                        logger.warning(f"[ithome] API 状态码 {resp.status}: {url}")
                        return None
                    return await resp.text()
        except Exception as e:
            logger.error(f"[ithome] 请求 API 失败: {e}")
            return None

    async def _summarize(self, content: str) -> str:
        """生成 AI 总结；失败或未启用返回占位文本。"""
        if not self.config.get("ai_summary_enabled", True):
            return "（AI 总结未启用）"

        prompt_tmpl = self.config.get(
            "summary_prompt", "请用一行、不超过40字总结以下新闻：{content}"
        )
        # 控制送入模型的正文长度
        prompt = prompt_tmpl.replace("{content}", content[:1500])

        base_url = (self.config.get("openai_base_url") or "").strip()
        api_key = (self.config.get("openai_api_key") or "").strip()

        # 优先使用自定义 OpenAI 兼容端点
        if base_url and api_key:
            try:
                return await self._openai_chat(base_url, api_key, prompt)
            except Exception as e:
                logger.error(f"[ithome] 自定义端点总结失败: {e}")
                # 落到框架 provider 兜底

        # 回退：使用 AstrBot 框架当前配置的 LLM
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                return "（未配置可用的大模型）"
            resp = await provider.text_chat(prompt=prompt)
            text = getattr(resp, "completion_text", None) or str(resp)
            return self._one_line(text)
        except Exception as e:
            logger.error(f"[ithome] 框架 LLM 总结失败: {e}")
            return "（AI 总结生成失败）"

    async def _openai_chat(self, base_url: str, api_key: str, prompt: str) -> str:
        model = self.config.get("openai_model", "gpt-4o-mini")
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                text = data["choices"][0]["message"]["content"]
                return self._one_line(text)

    @staticmethod
    def _one_line(text: str) -> str:
        return " ".join((text or "").split()).strip() or "（无总结内容）"

    # ---------- 主入口 ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str or ""
        if "ithome.com" not in text:
            return

        newsid = self._extract_newsid(text)
        if not newsid:
            return

        umo = event.unified_msg_origin
        whitelist = self.config.get("whitelist") or []
        blacklist = self.config.get("blacklist") or []
        if whitelist and umo not in whitelist:
            return
        if umo in blacklist:
            return

        if self._dedupe(umo, newsid):
            logger.debug(f"[ithome] 新闻 {newsid} 近期已解析，跳过")
            return

        logger.debug(f"[ithome] 解析新闻 {newsid}")
        xml = await self._fetch_xml(self._api_url(newsid))
        if not xml:
            return

        title = self._tag(xml, "title")
        if not title:
            logger.warning(f"[ithome] 未解析到标题, newsid={newsid}")
            return

        detail_raw = self._tag(xml, "detail")
        body = self._clean_html(detail_raw)
        max_chars = int(self.config.get("body_max_chars", 1200) or 1200)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "……"

        header_image = self._tag(xml, "image") or self._first_img(detail_raw)

        data = {
            "title": title,
            "post_date": self._tag(xml, "postdate"),
            "author": self._tag(xml, "newsauthor") or self._tag(xml, "z"),
            "source": self._tag(xml, "newssource"),
            "header_image": header_image,
            "body": body or "（正文为空）",
            "ai_summary": await self._summarize(body or title),
        }

        if not self._template:
            await self.initialize()
        if not self._template:
            logger.error("[ithome] 模板不可用，无法渲染")
            return

        try:
            img_path = await self.html_render(
                tmpl=self._template,
                data=data,
                return_url=False,
                options={"type": "jpeg", "quality": 95, "full_page": True},
            )
        except Exception as e:
            logger.error(f"[ithome] 渲染图片失败: {e}")
            return

        if not img_path:
            logger.error("[ithome] 渲染返回空路径")
            return

        yield event.chain_result([Image.fromFileSystem(img_path)])
