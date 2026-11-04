# -*- coding: utf-8 -*-
"""解决 rebase 冲突：取远端新代码 + 我们的面板与版本号"""
import re
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\Desktop\agent-control-tower")


def strip_conflict(text: str, choose: str) -> str:
    """choose: 'ours' (HEAD == upstream after rebase) or 'theirs' (our commit)"""
    out = []
    mode = None
    for line in text.splitlines(keepends=True):
        if line.startswith("<<<<<<<"):
            mode = "ours"
            continue
        if line.startswith("=======") and mode:
            mode = "theirs"
            continue
        if line.startswith(">>>>>>>") and mode:
            mode = None
            continue
        if mode is None or mode == choose:
            out.append(line)
    return "".join(out)


# 1) __init__.py -> 0.5.0
p = ROOT / "src/control_tower/__init__.py"
t = strip_conflict(p.read_text(encoding="utf-8"), "theirs")
p.write_text(t, encoding="utf-8")

# 2) config.py -> 取上游（CONFIG_NAMES 已含 tower.json）
p = ROOT / "src/control_tower/config.py"
t = strip_conflict(p.read_text(encoding="utf-8"), "ours")
p.write_text(t, encoding="utf-8")

# 3) pyproject.toml -> 0.5.0，去重重复行
p = ROOT / "pyproject.toml"
t = strip_conflict(p.read_text(encoding="utf-8"), "theirs")
t = re.sub(r'(version = "0\.5\.0"\n)(version = "0\.5\.0"\n)', r"\1", t)
p.write_text(t, encoding="utf-8")

# 4) .gitignore -> 两边都保留，去重
p = ROOT / ".gitignore"
t = p.read_text(encoding="utf-8")
t = strip_conflict(t, "ours")
add = "\ndocs/assets/demo-tower/\n"
if "docs/assets/demo-tower" not in t:
    t = t.rstrip("\n") + add
p.write_text(t, encoding="utf-8")

# 校验
for rel in ["src/control_tower/__init__.py", "src/control_tower/config.py", "pyproject.toml", ".gitignore"]:
    txt = (ROOT / rel).read_text(encoding="utf-8")
    assert "<<<<<<<" not in txt and ">>>>>>>" not in txt, rel
    print(f"resolved: {rel}")

print("version lines:", re.findall(r'version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))[:2])
