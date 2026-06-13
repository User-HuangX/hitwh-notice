from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp.web as web

logger = logging.getLogger(__name__)

CONFIG_PATHS = [
    "/home/hx/Astrbot/data/config/astrbot_plugin_hitwh_info_config.json",
    "/home/hx/Astrbot/data/config/astrbot_plugin_hitwh_info.json",
]

DEPRECATED_CONFIG_KEYS = {
    "my_class",
    "website_urls",
    "education_urls",
    "qq_groups",
    "qq_channels",
}

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HITWH 教务配置</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;color:#333;min-height:100vh}
.header{background:linear-gradient(135deg,#1a56db,#1e40af);color:#fff;padding:24px;text-align:center}
.header h1{font-size:1.5rem;margin-bottom:4px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);margin:20px auto;max-width:640px;padding:24px}
.card h2{font-size:1.1rem;margin-bottom:16px;color:#1a56db}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:.9rem;color:#666;margin-bottom:4px}
.form-group input{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:.95rem;outline:none;transition:border-color .2s}
.form-group input:focus{border-color:#1a56db}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border:none;border-radius:8px;font-size:.9rem;cursor:pointer;transition:all .2s}
.btn-primary{background:#1a56db;color:#fff}
.btn-primary:hover{background:#1e40af}
.btn-primary:disabled{background:#93c5fd;cursor:not-allowed}
.btn-success{background:#059669;color:#fff}
.btn-success:hover{background:#047857}
.btn-danger{background:#ef4444;color:#fff}
.btn-danger:hover{background:#dc2626}
.btn-sm{font-size:.8rem;padding:6px 12px}
.status{display:inline-block;padding:4px 10px;border-radius:20px;font-size:.8rem;font-weight:500}
.status-ok{background:#d1fae5;color:#065f46}
.status-none{background:#fee2e2;color:#991b1b}
.log{background:#1e293b;color:#e2e8f0;border-radius:8px;padding:12px;font-family:monospace;font-size:.82rem;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
.log .info{color:#93c5fd}
.log .success{color:#86efac}
.log .error{color:#fca5a5}
.hint{font-size:.82rem;color:#666;margin-top:8px}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
</style>
</head>
<body>
<div class="header"><h1>🎓 HITWH 教务配置</h1><p>Cookie 配置 | 手动同步 | AI 模型设置</p></div>

<div class="card">
<h2>📋 状态</h2>
<p>教务地址: <strong id="base-url">-</strong></p>
<p>Token: <span id="token-status" class="status status-none">未配置</span></p>
<p id="token-preview" style="margin-top:4px;font-size:.8rem;color:#999"></p>
</div>

<div class="card">
<h2>🔄 手动同步教务数据</h2>
<div class="actions">
 <button class="btn btn-primary btn-sm" onclick="syncOne('grades')">📊 同步成绩</button>
 <button class="btn btn-primary btn-sm" onclick="syncOne('schedule')">📅 同步课表</button>
 <button class="btn btn-primary btn-sm" onclick="syncOne('exams')">📝 同步考试</button>
 <button class="btn btn-primary btn-sm" onclick="syncOne('plan')">📘 同步培养方案</button>
 <button class="btn btn-success btn-sm" onclick="syncOne('index')">📇 重建知识库索引</button>
</div>
<div id="log-container" style="display:none;margin-top:16px">
 <div class="log" id="log"></div>
</div>
</div>

<div class="card">
<h2>🔑 配置教务 Cookie</h2>
<div class="form-group">
 <label>IVPN/WebVPN 教务地址</label>
 <input id="url-input" placeholder="http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118">
</div>
<div class="actions">
 <button class="btn btn-primary" id="capture-btn" onclick="capture()">🔄 启动浏览器登录捕获</button>
 <button class="btn btn-danger btn-sm" id="clear-btn" onclick="clearToken()" style="display:none">清除 Token</button>
</div>
</div>

<script>
function log(msg, cls) {
 const el = document.getElementById('log');
 const c = document.getElementById('log-container');
 c.style.display = 'block';
 el.innerHTML += `<span class="${cls||'info'}">${new Date().toLocaleTimeString()} ${msg}</span>\n`;
 el.scrollTop = el.scrollHeight;
}

async function loadStatus() {
 try {
  const r = await fetch('/status');
  const s = await r.json();
  document.getElementById('base-url').textContent = s.webvpn_base || '-';
  const tokenEl = document.getElementById('token-status');
  const previewEl = document.getElementById('token-preview');
  const clearBtn = document.getElementById('clear-btn');
  if (s.token) {
   tokenEl.textContent = '已配置';
   tokenEl.className = 'status status-ok';
   previewEl.textContent = s.token.substring(0, 80) + '...';
   clearBtn.style.display = 'inline-flex';
  } else {
   tokenEl.textContent = '未配置';
   tokenEl.className = 'status status-none';
   previewEl.textContent = '';
   clearBtn.style.display = 'none';
  }
  document.getElementById('url-input').value = s.webvpn_base || '';
 } catch(e) { console.error(e); }
}

async function syncOne(name) {
 log(`开始同步 ${name} ...`, 'info');
 try {
  const r = await fetch('/sync/' + name, {method:'POST'});
  const data = await r.json();
  if (data.ok) log(`${name} 同步完成: ${data.count} 条`, 'success');
  else log(`${name} 失败: ${data.error}`, 'error');
 } catch(e) { log(`${name} 请求失败: ` + e.message, 'error'); }
}

async function capture() {
 const btn = document.getElementById('capture-btn');
 btn.disabled = true;
 btn.textContent = '⏳ 等待用户登录...';
 document.getElementById('log').innerHTML = '';
 const url = document.getElementById('url-input').value.trim();
 if (!url) { log('请输入教务地址', 'error'); btn.disabled = false; btn.textContent = '🔄 启动浏览器登录捕获'; return; }
 log('启动浏览器...', 'info');
 try {
  const r = await fetch('/capture', {
   method: 'POST',
   headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({webvpn_base: url})
  });
  const data = await r.json();
  if (data.ok) {
   log('Cookie 捕获成功!', 'success');
   btn.textContent = '✅ 配置完成';
  } else {
   log(data.error || '捕获失败', 'error');
   btn.textContent = '🔄 重试';
  }
 } catch(e) {
  log('请求失败: ' + e.message, 'error');
  btn.textContent = '🔄 重试';
 }
 btn.disabled = false;
 loadStatus();
}

async function clearToken() {
 if (!confirm('确定要清除 Token 吗？')) return;
 try {
  await fetch('/clear', {method:'POST'});
  log('Token 已清除', 'info');
  loadStatus();
 } catch(e) { log('清除失败: ' + e.message, 'error'); }
}

loadStatus();
</script>
</body>
</html>"""


class WebConfig:
    def __init__(self, config: dict[str, Any], sync_callbacks: dict[str, Callable[[], Awaitable[Any]]] | None = None,
                 port: int = 8888) -> None:
        self.config = config
        self.sync_callbacks = sync_callbacks or {}
        self.port = port
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_post("/capture", self._handle_capture)
        self._app.router.add_post("/clear", self._handle_clear)
        self._app.router.add_post("/sync/{name}", self._handle_sync)
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("web_config started on port %s", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=HTML, content_type="text/html")

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "webvpn_base": self.config.get("webvpn_base", ""),
            "token": self.config.get("token", ""),
        })

    async def _handle_sync(self, request: web.Request) -> web.Response:
        name = request.match_info.get("name", "")
        cb = self.sync_callbacks.get(name)
        if cb is None:
            return web.json_response({"ok": False, "error": f"未知操作: {name}"})
        try:
            count = await cb()
            return web.json_response({"ok": True, "count": count})
        except Exception as e:
            logger.exception("web_sync_failed name=%s", name)
            return web.json_response({"ok": False, "error": str(e)})

    async def _handle_capture(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            webvpn_base = body.get("webvpn_base", "").strip().rstrip("/")
        except Exception:
            return web.json_response({"ok": False, "error": "无效请求"})
        if not webvpn_base:
            return web.json_response({"ok": False, "error": "请输入教务地址"})
        try:
            token = await _capture_cookie(webvpn_base)
        except Exception as e:
            logger.exception("capture_failed")
            return web.json_response({"ok": False, "error": str(e)})
        if not token:
            return web.json_response({"ok": False, "error": "未捕获到有效 Cookie"})
        self.config["token"] = token
        self.config["webvpn_base"] = webvpn_base
        _save_config(webvpn_base, token)
        logger.info("cookie_captured via web len=%s", len(token))
        return web.json_response({"ok": True, "token": token})

    async def _handle_clear(self, request: web.Request) -> web.Response:
        self.config["token"] = ""
        _save_config(self.config.get("webvpn_base", ""), "")
        return web.json_response({"ok": True})


async def _capture_cookie(webvpn_base: str, timeout: int = 120) -> str:
    import playwright.async_api as pw
    from playwright.async_api import TimeoutError as PwTimeout

    parsed = urlparse(webvpn_base)
    domain = parsed.hostname or ""

    async with pw.async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--ignore-certificate-errors"])
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        try:
            await page.goto(webvpn_base, wait_until="commit", timeout=30000)
        except Exception:
            pass

        try:
            await page.wait_for_url(
                lambda u: domain in u and "login" not in u.lower() and "cas" not in u.lower(),
                timeout=timeout * 1000,
            )
        except PwTimeout:
            await browser.close()
            raise RuntimeError("登录超时，请在浏览器中手动完成IVPN登录后访问教务页面")

        await asyncio.sleep(2)
        cookies = await context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("value"))
        await browser.close()

        if len(cookie_str) < 20:
            raise RuntimeError(f"Cookie 太短: {cookie_str[:50]}")
        return cookie_str


def _save_config(webvpn_base: str, token: str) -> None:
    for path_str in CONFIG_PATHS:
        p = Path(path_str)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                for key in DEPRECATED_CONFIG_KEYS:
                    data.pop(key, None)
                data["webvpn_base"] = webvpn_base
                data["token"] = token
                data.setdefault("sync_interval_hours", 1)
                data.setdefault("embedding_api_base", "https://api.siliconflow.cn/v1")
                data.setdefault("embedding_api_key", "")
                data.setdefault("embedding_model", "BAAI/bge-large-zh-v1.5")
                data.setdefault("embedding_dim", 1024)
                data.setdefault("rerank_api_base", "https://api.siliconflow.cn/v1")
                data.setdefault("rerank_api_key", "")
                data.setdefault("rerank_model", "BAAI/bge-reranker-v2-m3")
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                logger.info("config_saved path=%s", p)
            except Exception:
                logger.exception("config_save_failed path=%s", p)
