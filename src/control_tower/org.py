"""The two-layer org chart (v3 §6).

A lane is a leader plus its members. Members talk to their leader, leaders talk
to the lead; jumping a level is refused with the path that would have worked.

Nothing here changes a flat tower: the hierarchy is only enforced when at least
one role declares `leader`, `secretary` or `report_to` in tower.toml. A tower
that never opted in behaves exactly as before — bots may still write each other.
"""

from __future__ import annotations

from .config import TowerConfig

# roles that may talk to anybody even in a layered tower: the secretary is the
# one person who has to reach a lane when its leader is silent or gone.
DEFAULT_SECRETARY = "lead"


def enabled(cfg: TowerConfig) -> bool:
    """True when this tower actually declared a hierarchy."""
    return any(
        role.leader or role.secretary or role.report_to for role in cfg.roles.values()
    )


def lead(cfg: TowerConfig) -> str:
    for name, role in cfg.roles.items():
        if role.secretary:
            return name
    return cfg.peer_note_lead or DEFAULT_SECRETARY


def leader_of(cfg: TowerConfig, role: str) -> str:
    """The role one level up ("" when this role is already at the top)."""
    spec = cfg.roles.get(role)
    if not spec:
        return ""
    if spec.report_to:
        return spec.report_to
    if spec.leader or spec.secretary:
        return "" if spec.secretary else lead(cfg)
    # an undeclared member belongs to the lead until a leader claims it
    return lead(cfg) if role != lead(cfg) else ""


def members_of(cfg: TowerConfig, leader: str) -> list[str]:
    return [
        name
        for name, role in cfg.roles.items()
        if not role.secretary and (role.report_to == leader or (leader_of(cfg, name) == leader and not role.leader))
    ]


def chain(cfg: TowerConfig, role: str) -> list[str]:
    """role -> leader -> … -> lead. Cycle-safe."""
    path: list[str] = []
    seen: set[str] = set()
    current = role
    while current and current not in seen:
        path.append(current)
        seen.add(current)
        current = leader_of(cfg, current)
    return path


def check_path(cfg: TowerConfig, sender: str, to: str) -> dict[str, object]:
    """May `sender` write `to` directly? Returns {allow, reason, via}."""
    if not enabled(cfg):
        return {"allow": True, "reason": "", "via": []}
    if sender == to:
        return {"allow": False, "reason": "a role cannot write itself", "via": []}
    if sender not in cfg.roles or to not in cfg.roles:
        return {"allow": True, "reason": "unknown role: the role list is checked elsewhere", "via": []}

    secretary = lead(cfg)
    if sender == secretary:
        if cfg.roles[to].secretary:
            return {"allow": False, "reason": "", "via": []}
        via = leader_of(cfg, to)
        if via and via != to:
            return {
                "allow": False,
                "reason": (
                    f"the secretary writes leaders, not members: {to} reports to {via} "
                    f"— send it to {via} and let the lane hand it down"
                ),
                "via": [to, via],
            }
        return {"allow": True, "reason": "", "via": []}

    via = leader_of(cfg, sender) or secretary
    if to == via:
        return {"allow": True, "reason": "", "via": []}
    if cfg.roles[sender].leader and to == secretary:
        return {"allow": True, "reason": "", "via": []}
    if cfg.roles[sender].leader and to in members_of(cfg, sender):
        return {"allow": True, "reason": "", "via": []}
    # everything else is a jump: say which door was open
    door = via or secretary
    if door and door != to:
        return {
            "allow": False,
            "reason": (
                f"no direct line from {sender} to {to}: {sender} reports to {door}, "
                f"{door} reports to {secretary}. Write {door} first (tower say {door} …); "
                f"the lane forwards it."
            ),
            "via": chain(cfg, sender),
        }
    return {"allow": True, "reason": "", "via": []}


def tree(cfg: TowerConfig) -> list[dict[str, object]]:
    """Nested view for `tower roles --tree`: leaders with their members."""
    root = lead(cfg)
    out: list[dict[str, object]] = []
    placed: set[str] = set()

    def node(name: str) -> dict[str, object]:
        role = cfg.roles[name]
        return {
            "role": name,
            "leader": role.leader,
            "secretary": role.secretary,
            "members": [
                node(member) for member in members_of(cfg, name) if member not in placed
            ],
        }

    if root in cfg.roles:
        placed.add(root)
        out.append(node(root))
    for name, role in cfg.roles.items():
        if name in placed:
            continue
        if role.leader:
            placed.add(name)
            out.append(node(name))
    loose = [name for name in cfg.roles if name not in placed and name != root]
    for name in loose:
        out.append({"role": name, "leader": False, "secretary": False, "members": []})
    return out
