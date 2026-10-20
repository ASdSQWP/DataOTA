#!/usr/bin/env python3
"""Approximate preview renderer: .drawio (uncompressed) -> per-page PNG via Chromium."""
import sys, re, html, asyncio
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright

SRC, OUTDIR = sys.argv[1], sys.argv[2]
NAMES = sys.argv[3].split(",") if len(sys.argv) > 3 else None


def sty(s):
    d = {}
    for part in (s or "").split(";"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1); d[k] = v
        else:
            d[part] = "1"
    return d


def page_html(diagram):
    root = diagram.find("mxGraphModel/root")
    verts, edges = {}, []
    for el in root:
        if el.tag == "UserObject":
            cell = el.find("mxCell"); cid = el.get("id"); label = el.get("label", "")
        else:
            cell = el; cid = el.get("id"); label = el.get("value", "")
        if cell is None:
            continue
        st = sty(cell.get("style"))
        g = cell.find("mxGeometry")
        if cell.get("vertex") == "1" and g is not None:
            verts[cid] = dict(x=float(g.get("x", 0)), y=float(g.get("y", 0)), w=float(g.get("width", 0)),
                              h=float(g.get("height", 0)), st=st, label=label)
        elif cell.get("edge") == "1":
            pts = [(float(p.get("x")), float(p.get("y"))) for p in g.findall("Array/mxPoint")] if g is not None else []
            edges.append(dict(s=cell.get("source"), t=cell.get("target"), st=st, label=label, pts=pts))
    xs = [v["x"] for v in verts.values()] + [v["x"] + v["w"] for v in verts.values()]
    ys = [v["y"] for v in verts.values()] + [v["y"] + v["h"] for v in verts.values()]
    ox, oy = min(xs) - 30, min(ys) - 30
    W, H = max(xs) - ox + 30, max(ys) - oy + 30
    shapes, labels, lines = [], [], []

    def X(x): return x - ox
    def Y(y): return y - oy

    order = sorted(verts.items(), key=lambda kv: 0 if "swimlane" in kv[1]["st"] else 1)
    for cid, v in order:
        st = v["st"]; x, y, w, h = X(v["x"]), Y(v["y"]), v["w"], v["h"]
        fill = st.get("fillColor", "#ffffff"); stroke = st.get("strokeColor", "#000000")
        if fill == "none": fill = "none"
        if stroke == "none": stroke = "none"
        sw = st.get("strokeWidth", "1"); dash = 'stroke-dasharray="5 4"' if st.get("dashed") == "1" else ""
        if "text" in st:
            pass
        elif "swimlane" in st:
            ss = float(st.get("startSize", 30))
            sf = st.get("swimlaneFillColor", fill)
            shapes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" fill-opacity="0.35" stroke="{stroke}"/>')
            shapes.append(f'<rect x="{x}" y="{y}" width="{ss}" height="{h}" fill="{fill}" stroke="{stroke}"/>')
            labels.append(f'<div style="position:absolute;left:{x}px;top:{y}px;width:{ss}px;height:{h}px;display:flex;align-items:center;justify-content:center;'
                          f'font-weight:700;font-size:14px;color:#212529;writing-mode:vertical-rl;letter-spacing:2px">{v["label"]}</div>')
            continue
        elif "rhombus" in st:
            shapes.append(f'<polygon points="{x+w/2},{y} {x+w},{y+h/2} {x+w/2},{y+h} {x},{y+h/2}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif "ellipse" in st:
            shapes.append(f'<ellipse cx="{x+w/2}" cy="{y+h/2}" rx="{w/2}" ry="{h/2}" fill="{fill}" stroke="{stroke}"/>')
        elif st.get("shape") == "cylinder3":
            e = 8 if h > 30 else 4
            shapes.append(f'<path d="M{x},{y+e} A{w/2},{e} 0 0 1 {x+w},{y+e} L{x+w},{y+h-e} A{w/2},{e} 0 0 1 {x},{y+h-e} Z" fill="{fill}" stroke="{stroke}"/>'
                          f'<path d="M{x},{y+e} A{w/2},{e} 0 0 0 {x+w},{y+e}" fill="none" stroke="{stroke}"/>')
        elif st.get("shape") == "point":
            continue
        else:
            r = 0
            if st.get("rounded") == "1":
                r = min(w, h) * float(st.get("arcSize", 15)) / 100 if "arcSize" in st else 8
            shapes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {dash}/>')
        if v["label"]:
            align = st.get("align", "center"); va = st.get("verticalAlign", "middle")
            jc = {"left": "flex-start", "center": "center", "right": "flex-end"}[align]
            ai = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[va]
            fs = st.get("fontSize", "12"); fc = st.get("fontColor", "#212529")
            fw = "700" if st.get("fontStyle") == "1" else "400"
            pl = st.get("spacingLeft", "4"); pt = st.get("spacingTop", "2")
            if st.get("horizontal") == "0":
                extra = "writing-mode:vertical-rl;letter-spacing:2px;"
            else:
                extra = ""
            labels.append(f'<div style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;display:flex;justify-content:{jc};align-items:{ai};'
                          f'box-sizing:border-box;padding:{pt}px 4px 2px {pl}px;font-size:{fs}px;color:{fc};font-weight:{fw};text-align:{align};line-height:1.3;overflow:visible;{extra}">'
                          f'<div>{v["label"]}</div></div>')

    def side_pt(v, rx, ry):
        return (v["x"] + v["w"] * rx, v["y"] + v["h"] * ry)

    def auto_side(v, towards):
        cx, cy = v["x"] + v["w"] / 2, v["y"] + v["h"] / 2
        dx, dy = towards[0] - cx, towards[1] - cy
        if abs(dx) * v["h"] > abs(dy) * v["w"]:
            return (1, .5) if dx > 0 else (0, .5)
        return (.5, 1) if dy > 0 else (.5, 0)

    for e in edges:
        s, t = verts.get(e["s"]), verts.get(e["t"])
        if not s or not t:
            continue
        st = e["st"]
        tc = (t["x"] + t["w"] / 2, t["y"] + t["h"] / 2); sc = (s["x"] + s["w"] / 2, s["y"] + s["h"] / 2)
        ex = (float(st["exitX"]), float(st["exitY"])) if "exitX" in st else auto_side(s, e["pts"][0] if e["pts"] else tc)
        en = (float(st["entryX"]), float(st["entryY"])) if "entryX" in st else auto_side(t, e["pts"][-1] if e["pts"] else sc)
        p0, p1 = side_pt(s, *ex), side_pt(t, *en)
        horiz0 = ex[0] in (0, 1) and ex[1] not in (0, 1)
        horiz1 = en[0] in (0, 1) and en[1] not in (0, 1)
        seq = [p0] + e["pts"] + [p1]
        path = [p0]
        for i in range(1, len(seq)):
            a, b = path[-1], seq[i]
            if abs(a[0] - b[0]) > 0.5 and abs(a[1] - b[1]) > 0.5:
                first = i == 1; last = i == len(seq) - 1
                if first and last:
                    if horiz0 and horiz1:
                        mx = (a[0] + b[0]) / 2; path += [(mx, a[1]), (mx, b[1])]
                    elif not horiz0 and not horiz1:
                        my = (a[1] + b[1]) / 2; path += [(a[0], my), (b[0], my)]
                    elif horiz0:
                        path.append((b[0], a[1]))
                    else:
                        path.append((a[0], b[1]))
                elif first:
                    path.append((b[0], a[1]) if horiz0 else (a[0], b[1]))
                elif last:
                    path.append((a[0], b[1]) if horiz1 else (b[0], a[1]))
                else:
                    path.append((b[0], a[1]))
            path.append(b)
        col = st.get("strokeColor", "#343a40"); dash = 'stroke-dasharray="6 4"' if st.get("dashed") == "1" else ""
        sw = st.get("strokeWidth", "1.4")
        d = "M" + " L".join(f"{X(px)},{Y(py)}" for px, py in path)
        mk = "url(#af)" if st.get("endFill") == "1" else "url(#ao)"
        start = 'marker-start="url(#ao_s)"' if "startArrow" in st else ""
        lines.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw}" {dash} marker-end="{mk}" {start} style="color:{col}"/>')
        if e["label"]:
            # label at midpoint of longest segment
            best = max(range(len(path) - 1), key=lambda i: abs(path[i][0] - path[i+1][0]) + abs(path[i][1] - path[i+1][1]))
            mx = (path[best][0] + path[best + 1][0]) / 2; my = (path[best][1] + path[best + 1][1]) / 2
            fc = st.get("fontColor", "#343a40")
            labels.append(f'<div style="position:absolute;left:{X(mx)}px;top:{Y(my)}px;transform:translate(-50%,-50%);background:#fff;'
                          f'padding:0 3px;font-size:11px;color:{fc};white-space:nowrap">{e["label"]}</div>')
    svg = (f'<svg width="{W}" height="{H}" style="position:absolute;left:0;top:0" xmlns="http://www.w3.org/2000/svg">'
           '<defs><marker id="af" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>'
           '<marker id="ao" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10" fill="none" stroke="context-stroke" stroke-width="1.5"/></marker>'
           '<marker id="ao_s" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10" fill="none" stroke="context-stroke" stroke-width="1.5"/></marker></defs>'
           + "".join(shapes) + "".join(lines) + "</svg>")
    body = (f'<html><head><meta charset="utf-8"><style>body{{margin:0;background:#fff;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif}}'
            f'font{{font-family:inherit}}</style></head><body><div style="position:relative;width:{W}px;height:{H}px">{svg}{"".join(labels)}</div></body></html>')
    return body, int(W), int(H)


async def main():
    tree = ET.parse(SRC)
    diagrams = tree.getroot().findall("diagram")
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(device_scale_factor=1.5)
        for i, dg in enumerate(diagrams):
            name = NAMES[i] if NAMES and i < len(NAMES) else f"page{i+1}"
            body, w, h = page_html(dg)
            await pg.set_viewport_size({"width": w, "height": h})
            await pg.set_content(body)
            await pg.screenshot(path=f"{OUTDIR}/{name}.png", full_page=True)
            print(name, w, h)
        await br.close()

asyncio.run(main())
