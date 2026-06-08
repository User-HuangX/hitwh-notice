from astrbot_plugin_hitwh_info.parser import HtmlParser
from astrbot_plugin_hitwh_info.fetchers.qq_group import QQGroupFetcher
from astrbot_plugin_hitwh_info.fetchers.qq_channel import QQChannelFetcher


def test_html_parser_extracts_title_links_and_clean_text():
    html = """
    <html><head><title>通知</title><script>bad()</script></head>
    <body><h1>学院新闻</h1><p>计算机学院举办讲座。</p><a href="/x">详情</a></body></html>
    """

    page = HtmlParser().parse(html, "https://example.edu/news/1")

    assert page.title == "通知"
    assert "bad" not in page.text
    assert "计算机学院举办讲座。" in page.text
    assert page.links == ["https://example.edu/x"]


def test_qq_group_fetcher_turns_configured_groups_into_atomic_facts():
    fetcher = QQGroupFetcher(groups=[{"group_id": "123456", "group_name": "计科2101通知群", "member_count": 45}])

    facts = fetcher.to_facts(fetcher.groups)

    assert facts == [
        {
            "fact": "QQ群\"计科2101通知群\"(群号:123456)是一个校园信息群，共45名成员。",
            "source_type": "qq_group",
            "source_name": "123456",
            "metadata": {"group_name": "计科2101通知群", "member_count": 45},
        }
    ]


def test_qq_channel_fetcher_turns_channels_into_atomic_facts():
    fetcher = QQChannelFetcher(channels=[{"guild_id": "g1", "guild_name": "HITWH计算机", "channels": ["通知公告", "新生答疑"]}])

    facts = fetcher.to_facts(fetcher.channels)

    assert facts[0]["fact"] == "QQ频道\"HITWH计算机\"包含子频道\"通知公告\"、\"新生答疑\"。"
    assert facts[0]["source_type"] == "qq_channel"
    assert facts[0]["source_name"] == "g1"
