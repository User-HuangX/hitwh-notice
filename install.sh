#!/bin/bash
set -e
echo "安装 AstrBot + HITWH 插件全部依赖..."

uv tool install astrbot \
  --with asyncpg \
  --with sqlalchemy \
  --with pgvector \
  --with more-itertools \
  --with tenacity \
  --with playwright \
  --with aiohttp \
  --with beautifulsoup4 \
  --with lxml \
  --with pydantic \
  --reinstall

echo "安装 Chromium 浏览器..."
uv tool run --from playwright playwright install chromium

echo "完成！启动 AstrBot: cd /home/hx/Astrbot && astrbot run"
