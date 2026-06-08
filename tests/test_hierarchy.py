import pytest

from astrbot_plugin_hitwh_info.hierarchy import HierarchyMatcher


class FakeDB:
    def __init__(self):
        self.nodes = []
        self.next_id = 1
        self.resolved = []

    async def find_node(self, parent_id, name):
        for node in self.nodes:
            if node["parent_id"] == parent_id and node["name"] == name:
                return node
        return None

    async def insert_node(self, parent_id, level, name, status):
        node = {
            "id": self.next_id,
            "parent_id": parent_id,
            "level": level,
            "name": name,
            "status": status,
        }
        self.next_id += 1
        self.nodes.append(node)
        return node["id"]

    async def list_nodes(self, level=None):
        return [n for n in self.nodes if level is None or n["level"] == level]

    async def get_descendants(self, node_id):
        descendants = [node_id]
        changed = True
        while changed:
            changed = False
            for node in self.nodes:
                if node["parent_id"] in descendants and node["id"] not in descendants:
                    descendants.append(node["id"])
                    changed = True
        return descendants

    async def get_ancestors(self, node_id):
        by_id = {n["id"]: n for n in self.nodes}
        current = by_id[node_id]
        out = []
        while current:
            out.append(current)
            current = by_id.get(current["parent_id"])
        return out


@pytest.mark.asyncio
async def test_bootstrap_creates_school_and_confirmed_colleges():
    db = FakeDB()
    matcher = HierarchyMatcher(db)

    school_id = await matcher.bootstrap(["计算机科学与技术学院", "海洋工程学院"])

    assert school_id == 1
    assert [(n["level"], n["name"], n["status"]) for n in db.nodes] == [
        ("学校", "哈尔滨工业大学（威海）", "confirmed"),
        ("院系", "计算机科学与技术学院", "confirmed"),
        ("院系", "海洋工程学院", "confirmed"),
    ]


@pytest.mark.asyncio
async def test_group_name_match_creates_confirmed_teacher_and_class_under_best_college():
    db = FakeDB()
    matcher = HierarchyMatcher(db)
    await matcher.bootstrap(["计算机科学与技术学院"])

    hierarchy_id = await matcher.match_source_name("计科2101王老师通知群")

    class_node = next(n for n in db.nodes if n["id"] == hierarchy_id)
    teacher_node = next(n for n in db.nodes if n["id"] == class_node["parent_id"])
    assert class_node["level"] == "班级"
    assert class_node["name"] == "计科2101"
    assert teacher_node["level"] == "导员"
    assert teacher_node["name"] == "王老师"


@pytest.mark.asyncio
async def test_scope_resolver_returns_college_descendants_for_my_college():
    db = FakeDB()
    matcher = HierarchyMatcher(db)
    await matcher.bootstrap(["计算机科学与技术学院"])
    class_id = await matcher.match_source_name("计科2101王老师通知群")

    ids = await matcher.resolve_scope("my_college", user_class="计科2101", query="我们院有哪些老师")

    assert set(ids) == set(await db.get_descendants(2))
    assert class_id in ids
