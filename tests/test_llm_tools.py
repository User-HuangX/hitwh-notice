import logging

from astrbot_plugin_hitwh_info.main import HitwhInfoPlugin


class FakeEmbedder:
    async def embed(self, query):
        return [0.1, 0.2]

    async def rerank(self, query, docs):
        return [{"index": i} for i in range(len(docs))]


class FakeDB:
    async def search_chunks(self, embedding):
        logging.getLogger(__name__).info("db_access")
        return [
            {
                "document_id": 1,
                "source_type": "grade",
                "content": "微积分 95分",
                "doc_content": "微积分 95分",
            },
            {
                "document_id": 2,
                "source_type": "exam",
                "content": "英语考试",
                "doc_content": "英语考试",
            },
        ]


def test_llm_tools_have_docstrings_astrbot_can_parse():
    tools = {
        "tool_search": "query",
        "tool_grades": "keyword",
        "tool_schedule": "query",
        "tool_exams": "query",
        "tool_plan": "keyword",
    }

    for method_name, arg_name in tools.items():
        doc = getattr(HitwhInfoPlugin, method_name).__doc__ or ""

        assert doc.strip(), f"{method_name} needs a docstring for AstrBot llm_tool"
        assert "Args:" in doc, f"{method_name} needs an Args section"
        assert f"{arg_name}(string):" in doc, f"{method_name} must describe {arg_name}"


async def test_tool_search_logs_successful_rag_recall(caplog):
    caplog.set_level(logging.INFO)
    plugin = HitwhInfoPlugin.__new__(HitwhInfoPlugin)
    plugin.db = FakeDB()
    plugin._embedder = FakeEmbedder()

    result = await plugin.tool_search("微积分")

    assert "微积分 95分" in result
    assert "rag_recall_start source=tool_search" in caplog.text
    assert "embedding_dim=2" in caplog.text
    assert "rag_recall_success source=tool_search" in caplog.text
    assert "candidates=2" in caplog.text
    assert "unique=2" in caplog.text
    assert "returned=2" in caplog.text
    assert caplog.text.index("rag_recall_start") < caplog.text.index("db_access")
    assert caplog.text.index("db_access") < caplog.text.index("rag_recall_success")
