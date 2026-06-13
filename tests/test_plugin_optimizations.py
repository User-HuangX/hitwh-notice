import json
import logging
import asyncio
from pathlib import Path

import astrbot_plugin_hitwh_info.main as plugin_main
from astrbot_plugin_hitwh_info.main import HitwhInfoPlugin


class FakeEvent:
    def __init__(self, msg: str = ""):
        self.msg = msg

    def get_message_str(self):
        return self.msg

    def plain_result(self, text: str):
        return text

    def get_group_id(self):
        return ""

    def get_sender_id(self):
        return "user-1"

    def get_sender_name(self):
        return "Alice"


class FakeEmbedder:
    async def embed(self, query):
        return [0.1, 0.2]

    async def rerank(self, query, docs):
        return [{"index": i} for i in range(len(docs))]


class SearchDB:
    async def search_chunks(self, embedding):
        return [{
            "document_id": 1,
            "source_type": "grade",
            "content": "高等数学 90分",
            "doc_content": "高等数学 90分",
        }]


class EmptySearchDB:
    async def search_chunks(self, embedding):
        return []


class MessageDB:
    def __init__(self):
        self.messages = []

    async def upsert_qq_group(self, group_id, group_name, member_count=0):
        return 1

    async def insert_qq_message(self, **kwargs):
        self.messages.append(kwargs)


async def collect_async_gen(gen):
    items = []
    async for item in gen:
        items.append(item)
    return items


async def test_cmd_search_returns_results_once():
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.db = SearchDB()
    plugin._embedder = FakeEmbedder()

    results = await collect_async_gen(plugin.cmd_search(FakeEvent("/搜索 高等数学")))

    assert len(results) == 2
    assert results[0] == "🔍 正在语义搜索..."
    assert results[1].count("高等数学 90分") == 1


async def test_tool_search_logs_empty_rag_recall(caplog):
    caplog.set_level(logging.INFO)
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.db = EmptySearchDB()
    plugin._embedder = FakeEmbedder()

    result = await plugin.tool_search("不存在")

    assert result == ""
    assert "rag_recall_start source=tool_search" in caplog.text
    assert "rag_recall_empty source=tool_search" in caplog.text
    assert "candidates=0" in caplog.text


async def test_set_token_persists_config(monkeypatch):
    saved = {"called": False}

    class Config(dict):
        def save_config(self):
            saved["called"] = True

    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.config = Config({"webvpn_base": "http://base"})

    results = await collect_async_gen(plugin.cmd_set_token(FakeEvent("/set_token TWFID=abc123456789")))

    assert plugin.config["token"] == "TWFID=abc123456789"
    assert saved["called"] is True
    assert "已更新" in results[0]


def test_start_timers_disabled_when_interval_non_positive(caplog):
    caplog.set_level(logging.INFO)
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.config = {"sync_interval_hours": 0}

    plugin._start_timers()

    assert plugin._tasks == []
    assert "auto_sync_disabled" in caplog.text


async def test_llm_grades_syncs_once_when_database_empty(monkeypatch):
    class GradeDB:
        def __init__(self):
            self.calls = 0

        async def query_grades(self, keyword=""):
            self.calls += 1
            if self.calls == 1:
                return []
            return [{"semester": "2025秋", "course_name": "线代", "final_score": "91", "course_nature": "必修"}]

    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.db = GradeDB()
    plugin.config = {"token": "TWFID=abc", "webvpn_base": "http://base"}
    synced = {"called": False}

    async def fake_sync():
        synced["called"] = True
        return 1

    plugin._sync_grades = fake_sync

    result = await plugin.tool_grades("线代")

    assert synced["called"] is True
    assert "线代" in result


async def test_cmd_grades_reports_sync_failure_without_crashing():
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.config = {"token": "TWFID=abc"}

    async def failing_sync():
        raise RuntimeError("成绩页未加载出表格")

    plugin._sync_grades = failing_sync

    results = await collect_async_gen(plugin.cmd_grades(FakeEvent("成绩")))

    assert results == ["正在拉取最新成绩...", "⚠️ 成绩拉取失败：成绩页未加载出表格"]


async def test_private_messages_are_stored_and_indexed(monkeypatch):
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.db = MessageDB()
    plugin.config = {"group_whitelist": []}
    indexed = []

    async def fake_auto_index(user_id, content):
        indexed.append((user_id, content))

    plugin._auto_index_message = fake_auto_index

    plugin._tasks = []

    await plugin._on_message(FakeEvent("私聊也要进知识库"))
    if plugin._tasks:
        await asyncio.gather(*plugin._tasks)

    assert plugin.db.messages[0]["group_id"] == "private"
    assert plugin.db.messages[0]["content"] == "私聊也要进知识库"
    assert indexed == [("user-1", "私聊也要进知识库")]


def test_web_config_port_in_schema_and_startup(monkeypatch):
    schema = json.loads(Path("astrbot_plugin_hitwh_info/_conf_schema.json").read_text())
    assert schema["web_config_port"]["type"] == "int"
    assert schema["web_config_port"]["default"] == 8888

    instances = []

    class FakeWebConfig:
        def __init__(self, config, sync_callbacks=None, port=8888):
            self.port = port
            instances.append(self)

        async def start(self):
            pass

    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.config = {"web_config_port": 8899}
    plugin_main.WebConfig = FakeWebConfig

    async def run():
        await plugin._start_web_config()

    import asyncio
    asyncio.run(run())

    assert instances[0].port == 8899


def test_old_education_fetcher_removed_from_public_docs_and_package():
    assert not Path("astrbot_plugin_hitwh_info/fetchers/education.py").exists()
    assert "education.py" not in Path("README.md").read_text()
    assert "education.py" not in Path("astrbot_plugin_hitwh_info/README.md").read_text()


def test_edu_browser_ignores_certificate_errors():
    source = Path("astrbot_plugin_hitwh_info/fetchers/_edu_base.py").read_text()
    assert 'args=["--ignore-certificate-errors"]' in source
    assert "ignore_https_errors=True" in source
