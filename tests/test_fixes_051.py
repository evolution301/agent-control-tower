"""Regression tests for the three defects reported from the PRL machine."""

import json

from control_tower import cli
from control_tower.adapters.base import AdapterRequest
from control_tower.adapters.dsh import DshAdapter
from control_tower.config import load_config
from control_tower.roles import add_role, list_roles


# ---------------------------------------------------------------- 1. titles
def test_title_lookup_falls_back_to_the_disk_projcache(tmp_path):
    """`session/list` may answer with empty `projections.rows` (no title).

    The file cache still carries `record.rows.title.val`, so the lookup must read
    it instead of creating a duplicate session with the same title.
    """
    cache = tmp_path / "projcache"
    cache.mkdir()
    real_id = "af2913d5-941a-491d-88bb-5acbfa64e4b9"
    (cache / (real_id + ".json")).write_text(
        json.dumps(
            {
                "record": {
                    "identity": {"cwd": str(tmp_path)},
                    "rows": {"title": {"val": "PRL 后端"}},
                }
            }
        ),
        encoding="utf-8",
    )
    adapter = DshAdapter(
        base_url="http://127.0.0.1:1",
        sessions={"backend": "auto"},
        role_titles={"backend": "PRL 后端"},
        projcache_dir=str(cache),
    )

    calls = []

    def fake_rpc(method, args, timeout=90):
        calls.append(method)
        if method == "session/list":
            # A row without any projections, exactly what the desktop host returned.
            return {
                "result": {
                    "ok": True,
                    "value": {
                        "items": [
                            {
                                "sessionId": "session-rpc-row",
                                "cwd": str(tmp_path),
                                "updatedAt": 1,
                                "projections": {"rows": {}},
                            }
                        ]
                    },
                }
            }
        return {"result": {"ok": True}}

    adapter.rpc = fake_rpc
    request = AdapterRequest(role="backend", prompt="x", cwd=str(tmp_path), task_id="T-0001")
    session_id, how = adapter.resolve_session(request)

    assert session_id == "session-" + real_id
    assert how == "title-match"
    assert "session/create" not in calls


def test_rpc_title_wins_over_the_disk_cache(tmp_path):
    cache = tmp_path / "projcache"
    cache.mkdir()
    (cache / "11111111-1111-1111-1111-111111111111.json").write_text(
        json.dumps({"record": {"identity": {"cwd": str(tmp_path)}, "rows": {"title": {"val": "PRL 后端"}}}}),
        encoding="utf-8",
    )
    adapter = DshAdapter(
        base_url="http://127.0.0.1:1",
        sessions={"backend": "auto"},
        role_titles={"backend": "PRL 后端"},
        projcache_dir=str(cache),
    )
    adapter.rpc = lambda method, args, timeout=90: {
        "result": {
            "ok": True,
            "value": {
                "items": [
                    {
                        "sessionId": "session-from-rpc",
                        "cwd": str(tmp_path),
                        "updatedAt": 5,
                        "projections": {"values": {"title": "PRL 后端"}},
                    }
                ]
            },
        }
    }
    request = AdapterRequest(role="backend", prompt="x", cwd=str(tmp_path), task_id="T-0001")
    assert adapter.resolve_session(request) == ("session-from-rpc", "title-match")


# ---------------------------------------------------------------- 2. BOM
def test_description_is_cleaned_of_bom_and_heading(tmp_path):
    path = tmp_path / "tower.toml"
    path.write_text("name: p\nworkspace: .\nroles:\n  a:\n    description: x\n", encoding="utf-8")
    add_role(path, "backend", description="\ufeff# PRL 后端\n")
    cfg = load_config(tmp_path)
    assert cfg.roles["backend"].description == "PRL 后端"
    rows = {row["name"]: row for row in list_roles(cfg)}
    assert rows["backend"]["description"] == "PRL 后端"


# ---------------------------------------------------------------- 3. PATH
def test_doctor_flags_a_verify_command_missing_from_path(tmp_path, capsys):
    path = tmp_path / "tower.toml"
    path.write_text(
        'name: p\nworkspace: .\nroles:\n  a:\n    description: x\n\n'
        'verify_commands:\n  - "definitely-not-a-real-binary-xyz --version"\n',
        encoding="utf-8",
    )
    rc = cli.main(["--workspace", str(tmp_path), "doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "is not on PATH" in out


def test_doctor_accepts_a_shell_builtin_verify_command(tmp_path, capsys):
    path = tmp_path / "tower.toml"
    path.write_text(
        'name: p\nworkspace: .\nroles:\n  a:\n    description: x\n\n'
        'verify_commands:\n  - "echo ok"\n',
        encoding="utf-8",
    )
    rc = cli.main(["--workspace", str(tmp_path), "doctor"])
    out = capsys.readouterr().out
    assert "problems: none" in out
    assert rc == 0
