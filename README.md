# 投票系统 (yunxing-tutorial)

无账号、链接即入口的独立 Web 投票应用，面向团队/社区低风险决策。需求见
GitHub issue [#5](https://github.com/raptoravis/yunxing-tutorial/issues/5)。

## 特性

- **四种机制**：单选 / 多选 / 排序（Borda 计分）/ 打分（1–5 取平均）
- **链接即入口**：创建后得「公开投票链接」+「含密钥的管理链接」，投票者无需登录
- **投后实时结果**：原生 SSE 广播，投完即看走向，结果随新票动态更新
- **可选「截止前隐藏结果」**：抑制从众与刷票放大
- **轻度去重 + 软性 per-IP 限速**：cookie 去重，关闭前可改票
- **管理端 capability-URL**：密钥置于 URL fragment、服务端只存哈希、constant-time 校验

## 技术栈

FastAPI + Jinja2 服务端渲染 + HTMX/Alpine.js + 原生 SSE + SQLite（SQLAlchemy 2.x，WAL）。
单进程部署；多 worker/多机时将进程内 pub/sub 换为 Redis（接口已预留）。

## 运行

```bash
uv sync
uv run uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 创建投票。

生产环境设 `VOTING_FORCE_HTTPS=1` 启用 HTTPS 重定向 + HSTS（管理密钥安全前提）。

## 测试

```bash
uv run pytest
```

## 目录

- `app/` — 应用代码（models、tally、broker、security、dedup、routes/）
- `templates/` — Jinja2 模板与 ballot/结果 partial
- `tests/` — pytest 用例（创建 / 投票 / 计票 / 结果 / 管理 / 生命周期）
