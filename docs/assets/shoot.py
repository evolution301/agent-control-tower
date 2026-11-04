# -*- coding: utf-8 -*-
"""截图 + 自动裁掉空白边框，再合成 GIF"""
import json
import os
import subprocess
import time

from PIL import Image, ImageChops

ROOT = r"C:\Users\Administrator\Desktop\agent-control-tower"
GEN = os.path.join(ROOT, "docs", "assets", "generated")
OUT = os.path.join(ROOT, "docs", "assets")


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout


def bsk(*args):
    return run(["bsk", *args])


def trim(path, pad=24, bg=None):
    im = Image.open(path).convert("RGB")
    corner = bg or im.getpixel((2, 2))
    diff = ImageChops.difference(im, Image.new("RGB", im.size, corner))
    bbox = diff.getbbox()
    if bbox:
        left = max(0, bbox[0] - pad)
        top = max(0, bbox[1] - pad)
        right = min(im.width, bbox[2] + pad)
        bottom = min(im.height, bbox[3] + pad)
        im = im.crop((left, top, right, bottom))
    im.save(path, optimize=True)
    return im


info = bsk("session", "start", "--json")
sid = json.loads(info[info.find("{"):])["session_id"]
bsk("emulate", "--session", sid, "--width", "1240", "--height", "900", "--dpr", "2")

arch_html = "file:///" + os.path.join(GEN, "architecture.html").replace("\\", "/")
bsk("navigate", arch_html, "--session", sid)
time.sleep(2)
arch_png = os.path.join(OUT, "architecture.png")
bsk("screenshot", "--session", sid, "--out", arch_png)
trim(arch_png, pad=36)

frames = json.load(open(os.path.join(GEN, "frames.json"), encoding="utf-8"))
shots = []
for i, f in enumerate(frames, start=1):
    bsk("navigate", "file:///" + f.replace("\\", "/"), "--session", sid)
    time.sleep(1.6)
    shot = os.path.join(GEN, f"frame-{i}.png")
    bsk("screenshot", "--session", sid, "--out", shot)
    im = trim(shot, pad=26, bg=(15, 17, 21))
    shots.append(im)
    print(f"frame {i}: {im.size}")

bsk("session", "stop", sid)

# 统一宽度 1080
tw = 1080
resized = [im.resize((tw, int(im.height * tw / im.width)), Image.LANCZOS) for im in shots]
h = max(im.height for im in resized)
padded = []
for im in resized:
    canvas = Image.new("RGB", (tw, h), (15, 17, 21))
    canvas.paste(im, (0, 0))
    padded.append(canvas)

padded[0].save(
    os.path.join(OUT, "demo.gif"),
    save_all=True,
    append_images=padded[1:],
    duration=[2200] * (len(padded) - 1) + [3200],
    loop=0,
    optimize=True,
)
padded[1].save(os.path.join(OUT, "terminal-dispatch.png"), optimize=True)
padded[2].save(os.path.join(OUT, "terminal-accept.png"), optimize=True)
padded[3].save(os.path.join(OUT, "terminal-status.png"), optimize=True)
print("gif + stills written")
