#!/bin/bash
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${BLUE}[>]${NC} $1"; }

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════╗"
echo "║   HITWH 校园信息插件 - 一键安装脚本      ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ─── 1. PostgreSQL ───
echo ""; info "第1步: 配置 PostgreSQL 数据库"

if command -v psql &>/dev/null && pg_isready -q 2>/dev/null; then
    log "检测到本地 PostgreSQL 正在运行"
    PG_READY=1
elif command -v docker &>/dev/null; then
    warn "未检测到本地 PostgreSQL，使用 Docker 启动 pgvector..."
    read -p "  数据库密码 [hitwh123]: " PG_PASS
    PG_PASS=${PG_PASS:-hitwh123}
    docker rm -f hitwh_pg 2>/dev/null || true
    docker run -d --name hitwh_pg \
        -e POSTGRES_USER=hitwh -e POSTGRES_PASSWORD="$PG_PASS" \
        -e POSTGRES_DB=hitwh_test -p 5432:5432 \
        pgvector/pgvector:pg18 2>&1 | while read l; do :; done
    sleep 3
    log "PostgreSQL Docker 容器已启动 (hitwh_pg)"
    PG_READY=1
else
    err "未找到 PostgreSQL 或 Docker，请先安装其中之一"
    echo "  Docker: curl -fsSL https://get.docker.com | sh"
    exit 1
fi

read -p "  PostgreSQL DSN [postgresql://hitwh:hitwh123@localhost:5432/hitwh_test]: " PG_DSN
PG_DSN=${PG_DSN:-postgresql://hitwh:hitwh123@localhost:5432/hitwh_test}

# ─── 2. Python 依赖 ───
echo ""; info "第2步: 安装 Python 依赖"

DEPS="asyncpg sqlalchemy pgvector aiohttp beautifulsoup4 lxml playwright pydantic more-itertools tenacity"
log "安装: $DEPS"
uv tool install astrbot --with $DEPS --reinstall 2>&1 | tail -1
uv tool run --from playwright playwright install chromium 2>&1 | tail -1
log "依赖安装完成"

# ─── 3. 教务 Cookie ───
echo ""; info "第3步: 配置教务网 Cookie"

echo "  打开浏览器访问教务系统，F12 → Application → Cookies"
echo "  复制所有 Cookie（格式: key1=val1; key2=val2; ...）"
read -p "  Cookie/Token: " EDU_TOKEN
read -p "  教务地址 [http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118]: " EDU_BASE
EDU_BASE=${EDU_BASE:-http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118}

# ─── 4. AI 模型(可选) ───
echo ""; info "第4步: 配置 AI 嵌入/重排模型（可选，用于语义搜索）"
echo "  推荐硅基流动(siliconflow.cn)，注册即送免费额度"
read -p "  嵌入模型 API Key [留空跳过]: " EMB_KEY
read -p "  嵌入模型 [BAAI/bge-m3]: " EMB_MODEL
EMB_MODEL=${EMB_MODEL:-BAAI/bge-m3}
read -p "  重排模型 [BAAI/bge-reranker-v2-m3]: " RERANK_MODEL
RERANK_MODEL=${RERANK_MODEL:-BAAI/bge-reranker-v2-m3}

# ─── 5. 写入配置 ───
echo ""; info "第5步: 写入配置"

CONFIG_DIR="/home/hx/Astrbot/data/config"
mkdir -p "$CONFIG_DIR"

cat > "$CONFIG_DIR/astrbot_plugin_hitwh_info_config.json" << EOF
{
  "postgres_dsn": "$PG_DSN",
  "token": "$EDU_TOKEN",
  "webvpn_base": "$EDU_BASE",
  "sync_interval_hours": 1,
  "web_config_port": 8888,
  "embedding_api_base": "https://api.siliconflow.cn/v1",
  "embedding_api_key": "$EMB_KEY",
  "embedding_model": "$EMB_MODEL",
  "embedding_dim": 1024,
  "rerank_api_base": "https://api.siliconflow.cn/v1",
  "rerank_api_key": "$EMB_KEY",
  "rerank_model": "$RERANK_MODEL",
  "colleges": [],
  "group_whitelist": []
}
EOF
log "配置文件已写入: $CONFIG_DIR/astrbot_plugin_hitwh_info_config.json"

# ─── 6. 插件链接 ───
echo ""; info "第6步: 链接插件到 AstrBot"

PLUGIN_SRC="$(dirname "$(readlink -f "$0")")/astrbot_plugin_hitwh_info"
PLUGIN_DST="/home/hx/Astrbot/data/plugins/astrbot_plugin_hitwh_info"
rm -f "$PLUGIN_DST"
ln -s "$PLUGIN_SRC" "$PLUGIN_DST"
log "插件已链接: $PLUGIN_DST"

# ─── 完成 ───
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗"
echo "║         安装完成! 🎉                      ║"
echo "╚══════════════════════════════════════════╝${NC}"
echo ""
echo "启动 AstrBot:"
echo "  cd /home/hx/Astrbot && astrbot run"
echo ""
echo "启动后发以下命令初始化:"
echo "  /索引          ← 构建语义知识库(需先配好嵌入API Key)"
echo "  /成绩 /课程 /考试 /教学计划  ← 拉取教务数据"
echo ""
if [ -z "$EMB_KEY" ]; then
    warn "未配置嵌入模型 API Key，语义搜索功能不可用"
    echo "  后续在 Dashboard 插件配置中填入硅基流动 Key 即可"
fi
