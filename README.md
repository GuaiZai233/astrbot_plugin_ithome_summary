# astrbot_plugin_ithome_summary

一个 [AstrBot](https://astrbot.app) 插件：自动检测会话消息中的 **IT之家（ithome.com）** 新闻链接，解析全文并渲染成一张美观的图片卡片，卡片底部附带 **AI 一句话总结**。

## 功能

- 🔗 自动识别以下链接形态（无需任何指令，发链接即触发）：
  - `https://m.ithome.com/html/974846.htm`
  - `https://www.ithome.com/0/974/846.htm`
- 📰 通过官方接口 `api.ithome.com` 获取新闻标题、时间、作者、正文。
- 🖼️ 渲染为图片卡片：顶部头图 + 大标题 + 发布时间/作者 + 去格式化正文 + `🤖 AI 总结`。
- 🤖 AI 总结支持自定义 OpenAI 兼容端点；未配置时自动回退到 AstrBot 框架当前使用的大模型。
- ⚙️ 支持会话黑/白名单、防重复解析、正文长度限制。

## 配置项

| 配置 | 说明 |
| --- | --- |
| `ai_summary_enabled` | 是否生成 AI 总结 |
| `openai_base_url` | OpenAI 兼容端点，如 `https://api.openai.com/v1`；留空用框架内置 LLM |
| `openai_api_key` | 端点 API Key |
| `openai_model` | 模型名，默认 `gpt-4o-mini` |
| `summary_prompt` | 总结提示词，`{content}` 为正文占位符 |
| `body_max_chars` | 正文最大字数（超出截断） |
| `dedupe_interval` | 同会话同新闻防重复秒数 |
| `whitelist` / `blacklist` | 会话过滤 |

## 说明

正文中的内联图片会被剥离，仅在卡片顶部展示新闻头图，保证排版整洁。

## 许可

数据来源：IT之家。本插件仅用于个人学习与便捷阅读。
