"""Role packs for `tower role add --guided` (v3 §1).

Creating a bot is five pieces, not one: the role entry, the DSH session, the
sidebar title, the charter, and — in a layered tower — who it reports to. A new
team should not have to reverse-engineer that from a config file, so `--guided`
offers a pack that makes sense for the project and creates the pieces together.

Packs are plain data: adding one is a dict, not a code path.
"""

from __future__ import annotations

from typing import Any

ROLE_PACKS: dict[str, dict[str, Any]] = {
    "b2b-site": {
        "title": "B2B 外贸站（前后端 + 内容 + 质检）",
        "roles": [
            {"name": "lead", "description": "秘书：只派单、验收、汇报，不亲自干活", "secretary": True, "leader": True},
            {"name": "frontend", "description": "页面与样式", "leader": True, "report_to": "lead",
             "write_scopes": ["src/", "website/", "assets/"]},
            {"name": "backend", "description": "接口、插件、数据", "leader": True, "report_to": "lead",
             "write_scopes": ["src/", "plugins/", "inc/"]},
            {"name": "seo", "description": "关键词、结构化数据、内链", "report_to": "frontend",
             "write_scopes": ["content/", "website/"]},
            {"name": "content", "description": "文章与落地页文案", "report_to": "frontend",
             "write_scopes": ["content/"]},
            {"name": "qa", "description": "验收门：跑命令、量指标、挑刺", "leader": True, "report_to": "lead",
             "write_scopes": ["tests/", "reports/"]},
        ],
    },
    "service-api": {
        "title": "后端服务（接口 + 部署 + 质检）",
        "roles": [
            {"name": "lead", "description": "秘书：派单、验收、汇报", "secretary": True, "leader": True},
            {"name": "backend", "description": "接口与数据层", "leader": True, "report_to": "lead",
             "write_scopes": ["src/"]},
            {"name": "ops", "description": "部署、缓存、监控", "report_to": "backend", "write_scopes": ["deploy/"]},
            {"name": "qa", "description": "回归与验收门", "leader": True, "report_to": "lead",
             "write_scopes": ["tests/"]},
        ],
    },
    "content-site": {
        "title": "内容站（写作 + 配图 + 发布）",
        "roles": [
            {"name": "lead", "description": "秘书：选题排期、验收、汇报", "secretary": True, "leader": True},
            {"name": "writer", "description": "长文撰写", "leader": True, "report_to": "lead",
             "write_scopes": ["content/"]},
            {"name": "editor", "description": "事实核对与润色", "report_to": "writer", "write_scopes": ["content/"]},
            {"name": "publisher", "description": "排版、配图、上线", "report_to": "writer",
             "write_scopes": ["website/", "assets/"]},
        ],
    },
    "generic": {
        "title": "两个角色起步（秘书 + 执行）",
        "roles": [
            {"name": "lead", "description": "秘书：派单、验收、汇报", "secretary": True, "leader": True},
            {"name": "worker", "description": "执行角色", "leader": True, "report_to": "lead",
             "write_scopes": ["src/"]},
        ],
    },
}

PACK_ORDER = tuple(ROLE_PACKS)


def pack(name: str) -> dict[str, Any]:
    key = (name or "generic").strip().lower()
    if key not in ROLE_PACKS:
        raise KeyError(f"unknown role pack {name!r}; known: {', '.join(PACK_ORDER)}")
    return ROLE_PACKS[key]


def roles_for(name: str) -> list[dict[str, Any]]:
    return [dict(item) for item in pack(name)["roles"]]


def describe() -> str:
    return "\n".join(
        f"- {key}: {pack['title']}（{', '.join(item['name'] for item in pack['roles'])}）"
        for key, pack in ROLE_PACKS.items()
    )


def charter(role: dict[str, Any], tower_name: str = "") -> str:
    """The AGENT.md a new bot starts with — responsibilities, scope, report shape."""
    name = role.get("name", "role")
    scopes = ", ".join(role.get("write_scopes") or ["(none — this role reports, it does not edit)"])
    lines = [
        f"# {name}",
        "",
        f"{role.get('description') or '执行角色'}",
        "",
        f"塔台：{tower_name}" if tower_name else "塔台：—",
        "",
        "## 你能改什么",
        "",
        f"- {scopes}",
        "- 超出范围的写入先问组长，不要自己开新目录",
        "",
        "## 你的工作方式",
        "",
        "1. 收到任务先复述（restate），确认理解一致再动手",
        "2. 只做 brief 里写的事；发现更好的方案写进报告，不要顺手改",
        "3. 汇报必须带：summary / files / checks（命令 → 结果）/ self-review",
        "",
        "## 汇报给谁",
        "",
        f"- {role.get('report_to') or 'lead'}" if (role.get("report_to") or not role.get("secretary")) else "- 你直接对秘书（lead），不越级指挥组员",
        "",
        "## 沉淀",
        "",
        "- 做完把可复用的步骤写成 skill（触发条件 / 步骤 / 判断标准 / 踩过的坑）",
        "",
    ]
    return "\n".join(lines)
