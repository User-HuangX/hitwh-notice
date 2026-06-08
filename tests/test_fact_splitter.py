import pytest

from astrbot_plugin_hitwh_info.fact_splitter import FactSplitter


@pytest.mark.asyncio
async def test_rule_splitter_keeps_independent_chinese_facts_and_filters_noise():
    splitter = FactSplitter(min_length=8)

    facts = await splitter.split("学校简介。哈尔滨工业大学（威海）是工信部直属高校。\n计算机学院设有软件工程专业！  了解更多")

    assert facts == [
        "哈尔滨工业大学（威海）是工信部直属高校。",
        "计算机学院设有软件工程专业！",
    ]


@pytest.mark.asyncio
async def test_llm_splitter_parses_numbered_response_when_provider_is_available():
    class Provider:
        async def text_chat(self, prompt: str):
            return "1. 计算机学院位于H楼\n- 软件工程专业属于计算机学院\n\n无关"

    splitter = FactSplitter(llm_provider=Provider(), min_length=8)

    facts = await splitter.split("ignored")

    assert facts == ["计算机学院位于H楼", "软件工程专业属于计算机学院"]
