import astrbot_plugin_hitwh_info.main as plugin_main


class FakeDB:
    async def init_schema(self):
        pass

    async def close(self):
        pass


class FakeHierarchy:
    def __init__(self, db):
        self.db = db

    async def bootstrap(self, colleges):
        pass


async def test_initialize_starts_web_config_with_sync_callbacks(monkeypatch):
    instances = []

    class FakeWebConfig:
        def __init__(self, config, sync_callbacks=None, port=8888):
            self.config = config
            self.sync_callbacks = sync_callbacks or {}
            self.port = port
            self.started = False
            instances.append(self)

        async def start(self):
            self.started = True

    monkeypatch.setattr(plugin_main, "HitwhDB", lambda dsn: FakeDB())
    monkeypatch.setattr(plugin_main, "HierarchyMatcher", FakeHierarchy)
    monkeypatch.setattr(plugin_main, "WebConfig", FakeWebConfig, raising=False)

    plugin = plugin_main.HitwhInfoPlugin(None, {"sync_interval_hours": 0})

    await plugin.initialize()

    assert len(instances) == 1
    assert instances[0].started is True
    assert instances[0].port == 8888
    assert set(instances[0].sync_callbacks) == {"grades", "schedule", "exams", "plan", "index"}


async def test_terminate_stops_web_config(monkeypatch):
    instances = []

    class FakeWebConfig:
        def __init__(self, config, sync_callbacks=None, port=8888):
            self.stopped = False
            instances.append(self)

        async def start(self):
            pass

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(plugin_main, "HitwhDB", lambda dsn: FakeDB())
    monkeypatch.setattr(plugin_main, "HierarchyMatcher", FakeHierarchy)
    monkeypatch.setattr(plugin_main, "WebConfig", FakeWebConfig, raising=False)

    plugin = plugin_main.HitwhInfoPlugin(None, {"sync_interval_hours": 0})
    await plugin.initialize()

    await plugin.terminate()

    assert instances[0].stopped is True
