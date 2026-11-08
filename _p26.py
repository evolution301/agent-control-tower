import pathlib

p = pathlib.Path("tests/test_fixes_2026_11_09.py")
s = p.read_text(encoding="utf-8")
old = '''    pressure = adapter.session_pressure("s1")
    assert pressure == {"session": "s1", "contextPressure": 1234.0}'''
assert old in s, "envelope assert"
new = '''    pressure = adapter.session_pressure("s1")
    # v0.10.6 contract: the object is converted to percent + raw fields kept
    assert pressure == {"session": "s1", "pct": 1.23,
                        "pressureTokens": 1234, "contextWindow": 100000}'''
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("envelope test updated to the object contract")
