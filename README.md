# HITWH 校园信息插件

哈尔滨工业大学（威海）校园信息 AstrBot 插件，支持教务网数据自动拉取、PostgreSQL 存储、pgvector 语义搜索、LLM Function Calling 自动查询。

## 功能

### 教务数据查询

| 命令 | 功能 | 数据源 |
|---|---|---|
| `/成绩 [课程名]` | 查询历年成绩，支持课程名过滤 | 教务网 `cjcx/queryQmcj` + `cjcx/querySxwcj` |
| `/课程` | 本学期个人课表（每日每节课） | 教务网 `kbcx/queryGrkb` |
| `/考试` | 考试安排（时间/地点/座位号） | 教务网 `kscx/queryKcForXs` |
| `/教学计划 [课程名]` | 专业培养方案 | 教务网 `zxjh/queryZxkc` |
| `/hitwh_status` | 查看数据库、Token、同步和知识库状态 | 插件内部状态检查 |

### 语义搜索（RAG）

| 命令 | 功能 |
|---|---|
| `/索引` | 将成绩/课表/考试/培养方案拆分→嵌入→存入知识库 |
| `/搜索 <查询>` | 向量检索 + 重排序，返回 Top5 相关结果 |

### LLM 自动查询（Function Calling）

LLM 可自动调用以下 5 个工具，无需手动输命令：

| Tool | 触发示例 |
|---|---|
| `hitwh_grades` | "我微积分多少分"、"有哪些课挂了" |
| `hitwh_schedule` | "周五有什么课"、"M楼有哪些课" |
| `hitwh_exams` | "最近有什么考试"、"毛概在哪考" |
| `hitwh_plan` | "计科培养方案有哪些课"、"微积分几学分" |
| `hitwh_search` | 模糊语义查询，兜底方案 |

### QQ 消息采集

- 自动记录每个群的群号、群名、成员数
- **白名单机制**：配置 `group_whitelist` 后才保存消息内容，默认关闭
- 频道信息采集预留（NapCat HTTP API 集成中）

### Web 配置界面

`http://localhost:8888` — 可视化配置教务 Cookie，点击按钮自动打开浏览器完成 IVPN 登录并捕获 Cookie。

## 安装部署

### 环境要求

- PostgreSQL 16+ + pgvector 扩展
- Python 3.12+
- AstrBot v4.24+
- NapCat (QQ Bot) 或其他 OneBot v11 实现

### 安装步骤

```bash
# 1. 创建 pgvector 数据库
docker run -d --name hitwh_pg \
  -e POSTGRES_USER=hitwh \
  -e POSTGRES_PASSWORD=hitwh123 \
  -e POSTGRES_DB=hitwh_test \
  -p 5432:5432 \
  pgvector/pgvector:pg18

# 2. 安装插件到 AstrBot
cd /home/hx/Astrbot/data/plugins
ln -s /home/hx/PythonProject/hitwh_notice/astrbot_plugin_hitwh_info .

# 3. 安装依赖
cd /home/hx/PythonProject/hitwh_notice
uv sync

# 4. 安装 AstrBot + 插件依赖
uv tool install astrbot \
  --with playwright --with more-itertools --with tenacity \
  --with sqlalchemy --with asyncpg \
  --reinstall

# 5. 安装 Chromium（Playwright 需要）
uv tool run --from playwright playwright install chromium
```

### 配置

编辑 `Astrbot/data/config/astrbot_plugin_hitwh_info_config.json`：

```json
{
  "postgres_dsn": "postgresql://hitwh:hitwh123@localhost:5432/hitwh_test",
  "token": "",
  "webvpn_base": "http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118",
  "sync_interval_hours": 1,
  "web_config_port": 8888,
  "group_whitelist": [],
  "colleges": [],
  "embedding_api_base": "https://api.siliconflow.cn/v1",
  "embedding_api_key": "sk-你的key",
  "embedding_model": "BAAI/bge-large-zh-v1.5",
  "embedding_dim": 1024,
  "rerank_api_base": "https://api.siliconflow.cn/v1",
  "rerank_api_key": "sk-你的key",
  "rerank_model": "BAAI/bge-reranker-v2-m3"
}
```

**获取嵌入/重排 API Key：** 注册 [硅基流动](https://siliconflow.cn)（免费额度充足）。

**配置教务 Cookie：** 打开 `http://localhost:8888` → 点击「启动浏览器登录捕获」→ 在弹出的浏览器中登录 IVPN → Cookie 自动保存。

教务抓取接口路径已在代码中固定，包括成绩、课表、考试、培养方案；配置文件不再提供 `education_urls` 之类的 URL 列表。

### 配置项说明

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `postgres_dsn` | PostgreSQL 连接字符串 | - |
| `token` | 教务网 Cookie | 空（通过 Web 界面获取） |
| `webvpn_base` | IVPN/WebVPN 教务地址 | - |
| `sync_interval_hours` | 数据自动同步间隔（小时） | 1 |
| `web_config_port` | Web 配置页面端口 | 8888 |
| `group_whitelist` | QQ 群消息白名单（群号列表） | `[]`（不采集） |
| `colleges` | 可选院系列表；为空时使用内置学院列表 | `[]` |
| `embedding_api_base` | 嵌入模型 API 地址 | - |
| `embedding_api_key` | 嵌入模型 API Key | - |
| `embedding_model` | 嵌入模型名称 | - |
| `embedding_dim` | 嵌入向量维度 | 1024 |
| `rerank_api_base` | 重排模型 API 地址 | - |
| `rerank_api_key` | 重排模型 API Key | - |
| `rerank_model` | 重排模型名称 | - |

## 使用流程

```bash
# 1. 启动 AstrBot
cd /home/hx/Astrbot && astrbot run

# 2. 配置 Cookie（二选一）
#    方案A: Web界面 → http://localhost:8888 → 点击捕获
#    方案B: 手动粘贴 /set_token <cookie>

# 3. 拉取数据（首次）
/成绩          # 同步历年成绩
/课程          # 同步本学期课表
/考试          # 同步考试安排
/教学计划      # 同步培养方案

# 4. 构建知识库（首次，后续自动）
/索引

# 5. 之后每小时自动同步 + 直接问 LLM 即可
```

## 定时任务

4 个独立定时器，每小时各执行一次：
- 成绩同步 → `hitwh_grades`
- 课表同步 → `hitwh_schedule`
- 考试同步 → `hitwh_exams`
- 培养方案同步 → `hitwh_plan`

## 数据库结构

| 表名 | 内容 |
|---|---|
| `hitwh_grades` | 历年成绩（47 门） |
| `hitwh_schedule` | 课表（21 个时段） |
| `hitwh_exams` | 考试安排（9 场） |
| `hitwh_plan` | 培养方案 |
| `hitwh_knowledge` | 知识库（pgvector 向量索引） |
| `hitwh_qq_groups` | QQ 群信息 |
| `hitwh_qq_messages` | QQ 群消息（白名单控制） |
| `hitwh_hierarchy` | 学校层级结构 |

## 项目结构

```
astrbot_plugin_hitwh_info/
├── main.py              # 插件入口、命令、LLM Tools、定时器
├── db.py                # SQLAlchemy 模型 + 数据库 CRUD
├── models.py            # Pydantic 数据模型
├── embedding.py         # 嵌入 + 重排（SiliconFlow API）
├── fact_splitter.py     # 文本拆分为原子事实
├── hierarchy.py         # 学校层级匹配（学院/导员/班级）
├── web_config.py        # Web 配置界面（aiohttp）
├── parser.py            # HTML 解析
├── sources.py           # 默认配置常量
├── _utils.py            # 公共工具函数
└── fetchers/
    ├── _edu_base.py     # Playwright 浏览器启动
    ├── grades.py        # 成绩抓取
    ├── schedule.py      # 课表抓取
    ├── exams.py         # 考试抓取
    ├── plan.py          # 培养方案抓取
    ├── qq_collector.py  # QQ 群/频道信息采集
    └── website.py       # 学校官网抓取
```
