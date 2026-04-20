"""
Parse leaflet_caves_olympus.html → caves.json
Extracts: x, y, rotation, rank, tunnel, dang (and cave_rm / cave_uw variants)
"""

import re
import json

HTML_FILE = "leaflet_caves_olympus.html"
OUT_FILE  = "caves.json"

html = open(HTML_FILE, encoding="utf-8").read()

# Split on each marker div
marker_re = re.compile(
    r'<div class="leaflet-marker-icon custom-cave-icon[^>]+style="([^"]+)">'
    r'(.*?)'
    r'</div>\s*</div>',
    re.DOTALL
)

caves = []

for m in marker_re.finditer(html):
    style   = m.group(1)
    content = m.group(2)

    # --- position & rotation from transform ---
    t3d = re.search(r'translate3d\((\d+)px,\s*(\d+)px', style)
    if not t3d:
        continue
    x = int(t3d.group(1))
    y = int(t3d.group(2))

    rot_m = re.search(r'rotateZ\((-?[\d.]+)deg\)', style)
    rotation = float(rot_m.group(1)) if rot_m else 0.0

    # --- icons inside the wrapper ---
    imgs = re.findall(r'src="icon/([^"]+\.png)"', content)

    rank   = None
    tunnel = False
    dang   = False
    cave_rm = False
    cave_uw = False

    for img in imgs:
        rank_m = re.match(r'rank(\d)\.png', img)
        if rank_m:
            rank = int(rank_m.group(1))
        elif img == "cave_tn.png":
            tunnel = True
        elif img == "dang.png":
            dang = True
        elif img == "cave_rm.png":
            cave_rm = True
        elif img == "cave_uw.png":
            cave_uw = True

    cave = {"x": x, "y": y, "rotation": rotation, "rank": rank,
            "tunnel": tunnel, "dang": dang}
    if cave_rm:
        cave["cave_rm"] = True
    if cave_uw:
        cave["cave_uw"] = True

    caves.append(cave)

with open(OUT_FILE, "w", encoding="utf-8") as f:
    json.dump(caves, f, indent=2)

print(f"Wrote {len(caves)} caves -> {OUT_FILE}")
