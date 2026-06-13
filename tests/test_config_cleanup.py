import json
from pathlib import Path

from astrbot_plugin_hitwh_info import web_config


DEPRECATED_CONFIG_KEYS = {
    "my_class",
    "website_urls",
    "education_urls",
    "qq_groups",
    "qq_channels",
}


def test_config_schema_excludes_deprecated_keys():
    schema_path = Path("astrbot_plugin_hitwh_info/_conf_schema.json")
    schema = json.loads(schema_path.read_text())

    assert DEPRECATED_CONFIG_KEYS.isdisjoint(schema)


def test_install_script_excludes_deprecated_keys():
    script = Path("install.sh").read_text()

    for key in DEPRECATED_CONFIG_KEYS:
        assert f'"{key}"' not in script


def test_save_config_removes_deprecated_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "astrbot_plugin_hitwh_info_config.json"
    config_path.write_text(json.dumps({
        "postgres_dsn": "postgresql://example",
        "token": "old",
        "webvpn_base": "old-base",
        "education_urls": ["http://old"],
        "website_urls": ["http://old"],
        "qq_groups": [],
        "qq_channels": [],
        "my_class": "计科2101",
    }, ensure_ascii=False))
    monkeypatch.setattr(web_config, "CONFIG_PATHS", [str(config_path)])

    web_config._save_config("http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118", "TWFID=abc")

    saved = json.loads(config_path.read_text())
    assert saved["token"] == "TWFID=abc"
    assert saved["webvpn_base"] == "http://jwts-hitwh-edu-cn.ivpn.hitwh.edu.cn:8118"
    assert DEPRECATED_CONFIG_KEYS.isdisjoint(saved)
