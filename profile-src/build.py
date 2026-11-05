#!/usr/bin/env python3
"""
Builds the images and data shown on the STABLE organisation profile.

Inputs (this folder)
  institutions.csv       where the team works, one row per institution (first = coordinator)
  team.csv               team members, one row per person, in display order
  photos/<id>.jpg|png    optional portrait for each person (<id> as in team.csv)

Outputs (../profile)
  img/team/<id>.png                 round portraits used in the README
  img/map-<lang>-<theme>.png        team map (en/es, light/dark)
  img/stability-<lang>-<theme>.png  stability components figure
  img/banner-<lang>.png             header banner
  img/funders.png                   funder logos (from logos/)
  team-map.geojson                  interactive version of the map
  README.md, README.es.md           team table between the <!-- team:start/end --> marks

Portraits: photos/<id>.* first, then the GitHub avatar (github column), then initials.

Requirements: Python 3.9+, numpy, matplotlib, pillow
Usage:        python build.py
"""
from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import urllib.request
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, FancyBboxPatch
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
PROFILE = HERE.parent / "profile"
IMG = PROFILE / "img"
RAW = "https://raw.githubusercontent.com/STABLE-uv/.github/main/profile"
WORLD = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"
DPI = 100
LANGS = ("en", "es")

# --------------------------------------------------------------------- fonts


def _family(*names: str) -> str:
    have = {f.name for f in font_manager.fontManager.ttflist}
    return next((n for n in names if n in have), "DejaVu Sans")


FAMILY = _family("Inter", "Helvetica Neue", "Arial", "DejaVu Sans")
DISPLAY = _family("Inter Display", FAMILY)
plt.rcParams.update({"font.family": FAMILY})
_fonts: dict = {}


def pil_font(px: float, weight: str = "regular", family: str = FAMILY):
    key = (round(px), weight, family)
    if key not in _fonts:
        path = font_manager.findfont(FontProperties(family=family, weight=weight))
        _fonts[key] = ImageFont.truetype(path, round(px))
    return _fonts[key]


def text_w(s: str, px: float, weight: str = "regular", family: str = FAMILY) -> float:
    return pil_font(px, weight, family).getlength(s)


def wrap(s: str, px: float, width: float, weight: str = "regular", max_lines: int = 2) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        t = f"{cur} {word}".strip()
        if cur and text_w(t, px, weight) > width:
            lines.append(cur)
            cur = word
        else:
            cur = t
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return lines


def pt(px: float) -> float:
    """Pixel size -> matplotlib points at the figure dpi."""
    return px * 72 / DPI


# -------------------------------------------------------------------- themes

THEMES = {
    "light": dict(ocean="#eef3f6", land="#dde4e0", border="#f6f8f9", card="#ffffff",
                  edge="#d0d7de", text="#1f2328", muted="#59636e", shadow="#1f2328",
                  shadow_alpha=0.05, net="#8c959f", accent="#0e7c86", band="#0e7c86",
                  grid="#e4e8ec"),
    "dark": dict(ocean="#0f161e", land="#232e3a", border="#0f161e", card="#161b22",
                 edge="#30363d", text="#e6edf3", muted="#9198a1", shadow="#000000",
                 shadow_alpha=0.22, net="#6e7681", accent="#3cc4cd", band="#3cc4cd",
                 grid="#262c34"),
}


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1: str, c2: str, t: float) -> str:
    a, b = hex_rgb(c1), hex_rgb(c2)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rounded(path: Path, radius: int = 18) -> None:
    """Give a saved PNG transparent rounded corners."""
    im = Image.open(path).convert("RGBA")
    k = 4
    mask = Image.new("L", (im.width * k, im.height * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius * k, fill=255)
    im.putalpha(mask.resize(im.size, Image.LANCZOS))
    im.save(path, optimize=True)


def canvas(w: int, h: int, bg: str):
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
    fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def place_image(ax, im: Image.Image, x: float, y: float, size: float, z: float = 10) -> None:
    ax.imshow(np.asarray(im), extent=(x, x + size, y, y + size), zorder=z, interpolation="lanczos")


# ------------------------------------------------------------------ data in


def read_csv(name: str) -> list[dict]:
    with open(HERE / name, newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


# ----------------------------------------------------------------- portraits


def _photo(person: dict):
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = HERE / "photos" / f"{person['id']}.{ext}"
        if p.exists():
            return Image.open(p)
    user = person.get("github")
    if user:
        try:
            with urllib.request.urlopen(f"https://github.com/{user}.png?size=460", timeout=15) as r:
                return Image.open(io.BytesIO(r.read()))
        except Exception as e:  # offline, renamed account...
            print(f"  could not download the GitHub avatar of {user}: {e}")
    return None


def portrait(person: dict, color: str, size: int = 400) -> Image.Image:
    S = size * 2
    ring = round(S * 0.05)
    inner = S - 2 * ring
    photo = _photo(person)
    if photo is not None:
        photo = ImageOps.exif_transpose(photo).convert("RGB")
        w, h = photo.size
        if person.get("photo_crop"):  # "cx cy size": face centre and crop side, as fractions
            cx, cy, s = (float(v) for v in person["photo_crop"].split())
            side = int(min(w, h) * min(s, 1.0))
            left = min(max(int(cx * w - side / 2), 0), w - side)
            top = min(max(int(cy * h - side / 2), 0), h - side)
        else:
            side = min(w, h)
            left, top = (w - side) // 2, int((h - side) * 0.25)
        face = photo.crop((left, top, left + side, top + side)).resize((inner, inner), Image.LANCZOS)
    else:
        face = Image.new("RGB", (inner, inner), mix(color, "#ffffff", 0.80))
        label = person.get("initials") or "".join(w[0] for w in person["name"].split()[:2])
        ImageDraw.Draw(face).text((inner / 2, inner / 2), label.upper(), anchor="mm",
                                  font=pil_font(inner * 0.36, "semibold"),
                                  fill=mix(color, "#000000", 0.30))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse((0, 0, S - 1, S - 1), fill=color)
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
    out.paste(face, (ring, ring), mask)
    return out.resize((size, size), Image.LANCZOS)


# ------------------------------------------------------------------- basemap

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = math.sqrt(3) / 2


def equal_earth(lon, lat, lon0: float):
    lam = np.radians((np.asarray(lon, float) - lon0 + 180) % 360 - 180)
    th = np.arcsin(M * np.sin(np.radians(np.asarray(lat, float))))
    t2 = th * th
    t6 = t2 ** 3
    x = lam * np.cos(th) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = th * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, y


class Frame:
    """Equal Earth view cropped to a lon/lat window, in pixel units."""

    def __init__(self, lon0=-54.0, west=-128.0, east=20.0, south=-28.0, north=60.0, width=1600):
        self.lon0 = lon0
        (x0, x1), _ = equal_earth([west, east], [0, 0], lon0)
        _, (y0, y1) = equal_earth([lon0, lon0], [south, north], lon0)
        self.x0, self.y0 = x0, y0
        self.k = width / (x1 - x0)
        self.W, self.H = width, int(round((y1 - y0) * self.k))

    def __call__(self, lon, lat):
        x, y = equal_earth(lon, lat, self.lon0)
        return (x - self.x0) * self.k, (y - self.y0) * self.k


def load_world():
    cache = HERE / ".cache" / "countries-50m.json"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        print("  downloading the world basemap (Natural Earth 1:50m via world-atlas)")
        urllib.request.urlretrieve(WORLD, cache)
    topo = json.loads(cache.read_text(encoding="utf-8"))
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for a in topo["arcs"]:
        q = np.cumsum(np.asarray(a, dtype=float), axis=0)
        arcs.append(np.column_stack((q[:, 0] * sx + tx, q[:, 1] * sy + ty)))

    def ring(ids):
        parts = [arcs[i] if i >= 0 else arcs[~i][::-1] for i in ids]
        return np.vstack([parts[0]] + [p[1:] for p in parts[1:]])

    polygons, owners = [], {}
    for gi, g in enumerate(topo["objects"]["countries"]["geometries"]):
        polys = {"Polygon": [g.get("arcs")], "MultiPolygon": g.get("arcs")}.get(g.get("type"), [])
        for poly in polys:
            polygons.append(ring(poly[0]))  # outer ring; holes are other countries
            for r in poly:
                for i in r:
                    owners.setdefault(i if i >= 0 else ~i, set()).add(gi)
    borders = [arcs[j] for j, o in owners.items() if len(o) > 1]
    return polygons, borders


def clip_ring(pts: np.ndarray, box) -> np.ndarray | None:
    """Sutherland-Hodgman clip of a lon/lat ring to (west, east, south, north)."""
    x0, x1, y0, y1 = box
    if pts[:, 0].min() >= x0 and pts[:, 0].max() <= x1 and pts[:, 1].min() >= y0 and pts[:, 1].max() <= y1:
        return pts
    if pts[:, 0].max() < x0 or pts[:, 0].min() > x1 or pts[:, 1].max() < y0 or pts[:, 1].min() > y1:
        return None
    poly = [tuple(p) for p in pts]
    for axis, lim, keep_above in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        if not poly:
            break

        def inside(p):
            return p[axis] >= lim if keep_above else p[axis] <= lim

        out, prev = [], poly[-1]
        for cur in poly:
            if inside(cur) != inside(prev):
                t = (lim - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
            if inside(cur):
                out.append(cur)
            prev = cur
        poly = out
    return np.array(poly) if len(poly) >= 3 else None


def great_circle(a, b, n=160):
    def vec(lon, lat):
        lon, lat = math.radians(lon), math.radians(lat)
        return np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])

    p, q = vec(*a), vec(*b)
    w = math.acos(float(np.clip(p @ q, -1, 1)))
    t = np.linspace(0, 1, n)[:, None]
    v = (np.sin((1 - t) * w) * p + np.sin(t * w) * q) / math.sin(w)
    return np.degrees(np.arctan2(v[:, 1], v[:, 0])), np.degrees(np.arcsin(v[:, 2]))


# ----------------------------------------------------------------------- map

PAD = 22          # card padding
AVATAR = 90       # portrait diameter on the map
TEXT_W = 290      # width of the name/role column
NAME_PX, ROLE_PX = 25, 21
TITLE_PX, SUB_PX, PILL_PX = 27, 20.5, 16.5


def site_card(ax, T, site, members, portraits, lang, frame, anchor):
    width = AVATAR + 14 + TEXT_W
    title = [line for part in site[f"name_{lang}"].split("|")
             for line in wrap(part, TITLE_PX, width, "semibold")]
    unit = site.get(f"unit_{lang}", "")
    people = []
    for p in members:
        names = wrap(p["name"], NAME_PX, TEXT_W, "semibold")
        roles = wrap(p[f"role_{lang}"], ROLE_PX, TEXT_W)
        people.append((p, names, roles, max(AVATAR, len(names) * NAME_PX * 1.22 + 4 + len(roles) * ROLE_PX * 1.3)))
    head = 30 + 14 + len(title) * TITLE_PX * 1.2 + (4 + SUB_PX * 1.3 if unit else 0) + 18
    body = sum(h for *_, h in people) + 14 * (len(people) - 1)
    w, h = width + 2 * PAD, head + body + 2 * PAD
    x = float(site["card_x"]) * frame.W
    y = float(site["card_y"]) * frame.H
    color = site["color"]

    # leader line from the institution marker to the closest point of the card
    mx, my = anchor
    cx, cy = min(max(mx, x + 18), x + w - 18), min(max(my, y + 18), y + h - 18)
    ax.plot([mx, cx], [my, cy], color=color, lw=2, zorder=4, solid_capstyle="round")

    for i, grow in enumerate((10, 6, 3)):  # soft shadow
        ax.add_patch(FancyBboxPatch((x - grow / 2, y - grow / 2 - 3), w + grow, h + grow,
                                    boxstyle=f"round,pad=0,rounding_size={16 + grow / 2}",
                                    lw=0, fc=T["shadow"], alpha=T["shadow_alpha"] * (i + 1) / 3, zorder=5))
    card = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=16",
                          lw=1.2, ec=T["edge"], fc=T["card"], zorder=6)
    ax.add_patch(card)
    bar = ax.add_patch(plt.Rectangle((x, y + h - 7), w, 7, lw=0, fc=color, zorder=7))
    bar.set_clip_path(card)

    # header: country pill + city, institution, unit
    top = y + h - PAD
    pill = site[f"country_{lang}"].upper()
    pw = text_w(pill, PILL_PX, "semibold") + 22
    ax.add_patch(FancyBboxPatch((x + PAD, top - 30), pw, 30, boxstyle="round,pad=0,rounding_size=15",
                                lw=0, fc=color, zorder=8))
    ax.text(x + PAD + pw / 2, top - 15, pill, color="white", fontsize=pt(PILL_PX), weight="semibold",
            ha="center", va="center", zorder=9)
    ax.text(x + PAD + pw + 12, top - 15, site["city"], color=T["muted"], fontsize=pt(SUB_PX),
            ha="left", va="center", zorder=9)
    cur = top - 30 - 14
    for line in title:
        ax.text(x + PAD, cur, line, color=T["text"], fontsize=pt(TITLE_PX), weight="semibold",
                ha="left", va="top", zorder=9)
        cur -= TITLE_PX * 1.2
    if unit:
        ax.text(x + PAD, cur - 4, unit, color=T["muted"], fontsize=pt(SUB_PX), ha="left", va="top", zorder=9)
        cur -= 4 + SUB_PX * 1.3
    cur -= 9
    ax.plot([x + PAD, x + w - PAD], [cur, cur], color=T["edge"], lw=1, zorder=8)
    cur -= 9

    # people
    for p, names, roles, ph in people:
        place_image(ax, portraits[p["id"]], x + PAD, cur - ph / 2 - AVATAR / 2, AVATAR, z=9)
        block = len(names) * NAME_PX * 1.22 + 4 + len(roles) * ROLE_PX * 1.3
        ty = cur - (ph - block) / 2
        tx = x + PAD + AVATAR + 14
        for line in names:
            ax.text(tx, ty, line, color=T["text"], fontsize=pt(NAME_PX), weight="semibold",
                    ha="left", va="top", zorder=9)
            ty -= NAME_PX * 1.22
        ty -= 4
        for line in roles:
            ax.text(tx, ty, line, color=T["muted"], fontsize=pt(ROLE_PX), ha="left", va="top", zorder=9)
            ty -= ROLE_PX * 1.3
        cur -= ph + 14


def draw_map(sites, team, portraits, theme, lang, world, out: Path):
    T = THEMES[theme]
    fr = Frame()
    fig, ax = canvas(fr.W, fr.H, T["ocean"])
    box = (-179.9, 70.0, -70.0, 85.0)
    polygons, borders = world
    land = []
    for r in polygons:
        if np.abs(np.diff(r[:, 0])).max(initial=0) > 180:  # crosses the antimeridian (Fiji, Chukotka)
            continue
        r = clip_ring(r, box)
        if r is not None:
            x, y = fr(r[:, 0], r[:, 1])
            land.append(np.column_stack((x, y)))
    ax.add_collection(PolyCollection(land, facecolors=T["land"], edgecolors=T["land"], linewidths=0.6, zorder=1))
    lines = []
    for arc in borders:
        if (arc[:, 0].min() > box[0]) and (arc[:, 0].max() < box[1]) and (arc[:, 1].min() > box[2]):
            x, y = fr(arc[:, 0], arc[:, 1])
            lines.append(np.column_stack((x, y)))
    ax.add_collection(LineCollection(lines, colors=T["border"], linewidths=1.0, zorder=2))

    pos = {s["id"]: fr(float(s["lon"]), float(s["lat"])) for s in sites}
    hub = sites[0]  # links from the coordinating institution (first row) to the partners
    for b in sites[1:]:
        lon, lat = great_circle((float(hub["lon"]), float(hub["lat"])), (float(b["lon"]), float(b["lat"])))
        x, y = fr(lon, lat)
        ax.plot(x, y, color=T["net"], lw=1.6, ls=(0, (2, 5)), alpha=0.9, zorder=3, dash_capstyle="round")
    for s in sites:
        members = [p for p in team if p["site"] == s["id"]]
        if members:
            site_card(ax, T, s, members, portraits, lang, fr, pos[s["id"]])
    for s in sites:
        x, y = pos[s["id"]]
        ax.add_patch(Circle((x, y), 22, fc=s["color"], alpha=0.18, lw=0, zorder=10))
        ax.add_patch(Circle((x, y), 10, fc=s["color"], ec=T["card"], lw=3, zorder=11))
    ax.set_xlim(0, fr.W)
    ax.set_ylim(0, fr.H)
    fig.savefig(out, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    rounded(out)


# ------------------------------------------------------------ stability plot

STAB_TEXT = {
    "en": dict(x="Days after the chemical pulse", y="Response relative to the controls",
               control="Controls", treatment="Treated mesocosm", pulse="Chemical pulse",
               resistance=("Resistance", "change at the peak of effects (day 14)"),
               resilience=("Resilience", "speed of return (slope)"),
               recovery=("Recovery", "gap left at the end (day 70)"),
               invariability=("Invariability", "steadiness around the trend")),
    "es": dict(x="Días tras el pulso químico", y="Respuesta frente a los controles",
               control="Controles", treatment="Mesocosmo tratado", pulse="Pulso químico",
               resistance=("Resistencia", "cambio en el máximo efecto (día 14)"),
               resilience=("Resiliencia", "velocidad de retorno (pendiente)"),
               recovery=("Recuperación", "diferencia final (día 70)"),
               invariability=("Invariabilidad", "constancia en torno a la tendencia")),
}


def pchip(x, y, xs):
    """Monotone cubic interpolation (Fritsch-Carlson)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    h, d = np.diff(x), np.diff(y) / np.diff(x)
    m = np.zeros_like(y)
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, len(x) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    idx = np.clip(np.searchsorted(x, xs) - 1, 0, len(x) - 2)
    t = (xs - x[idx]) / h[idx]
    h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
    return h00 * y[idx] + h10 * h[idx] * m[idx] + h01 * y[idx + 1] + h11 * h[idx] * m[idx + 1]


def draw_stability(theme, lang, out: Path):
    T, L = THEMES[theme], STAB_TEXT[lang]
    W, H = 1600, 700
    left, right, bottom, top = 112, 330, 105, 40
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(T["card"])
    ax = fig.add_axes([left / W, bottom / H, 1 - (left + right) / W, 1 - (bottom + top) / H])
    ax.set_facecolor(T["card"])
    days = np.array([-7, 0, 7, 14, 28, 42, 56, 70])
    resp = np.array([0.02, 0.0, -0.80, -1.02, -0.58, -0.64, -0.33, -0.25])
    xs = np.linspace(-7, 70, 600)
    curve = pchip(days, resp, xs)
    post = days >= 14
    slope, icpt = np.polyfit(days[post], resp[post], 1)
    band = 1.6 * np.std(resp[post] - (icpt + slope * days[post]))
    tx = np.linspace(14, 70, 50)

    def trend(d):
        return icpt + slope * d

    ax.axhline(0, color=T["muted"], lw=2, zorder=2)
    ax.axvline(0, color=T["muted"], lw=1.4, ls=(0, (3, 4)), zorder=1)
    ax.fill_between(tx, trend(tx) - band, trend(tx) + band, color=T["band"], alpha=0.11, lw=0, zorder=1)
    ax.plot(tx, trend(tx), color=T["accent"], lw=2, ls=(0, (6, 4)), alpha=0.9, zorder=3)
    ax.plot(xs, curve, color=T["accent"], lw=4, zorder=4, solid_capstyle="round")
    ax.scatter(days[days != 0], resp[days != 0], s=80, color=T["accent"], ec=T["card"], lw=2.5, zorder=5)

    def label(x, y, key, ha="left"):
        head, body = L[key]
        ax.text(x, y, head, ha=ha, va="center", fontsize=pt(27), weight="semibold", color=T["text"], zorder=6)
        ax.text(x, y - 0.13, body, ha=ha, va="center", fontsize=pt(21), color=T["muted"], zorder=6)

    def leader(x0, y0, x1, y1):
        ax.plot([x0, x1], [y0, y1], color=T["muted"], lw=1.2, zorder=6)

    arrow = dict(arrowstyle="<|-|>", color=T["text"], lw=1.6, shrinkA=0, shrinkB=0, mutation_scale=18)
    ax.annotate("", (14, resp[3]), (14, 0), arrowprops=arrow, zorder=6)
    label(15.6, -0.17, "resistance")
    ax.annotate("", (70, resp[-1]), (70, 0), arrowprops=arrow, zorder=6)
    label(72.6, -0.07, "recovery")
    label(42, 0.40, "resilience")
    leader(48, 0.20, 51, trend(51) + 0.01)
    label(40, -1.08, "invariability")
    leader(50, -0.99, 52, trend(52) - band)
    ax.text(0.7, -1.33, L["pulse"], color=T["muted"], fontsize=pt(21), ha="left", va="center")

    # legend
    for i, (key, color, lw) in enumerate((("treatment", T["accent"], 4), ("control", T["muted"], 2))):
        y = 0.47 - i * 0.12
        ax.plot([1.6, 4.1], [y, y], color=color, lw=lw, solid_capstyle="round", zorder=6)
        ax.text(5.0, y, L[key], color=T["text"], fontsize=pt(21), ha="left", va="center", zorder=6)

    ax.set_xlim(-9, 72)
    ax.set_ylim(-1.42, 0.55)
    ax.set_xticks([-7, 0, 7, 14, 28, 42, 56, 70])
    ax.set_yticks([])
    ax.tick_params(axis="x", colors=T["muted"], labelsize=pt(21), length=0, pad=10)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(T["grid"])
    ax.spines["bottom"].set_linewidth(1.5)
    ax.set_xlabel(L["x"], color=T["muted"], fontsize=pt(22), labelpad=14)
    ax.set_ylabel(L["y"], color=T["muted"], fontsize=pt(22), labelpad=16)
    ax.grid(axis="x", color=T["grid"], lw=1)
    ax.set_axisbelow(True)
    fig.savefig(out, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    rounded(out)


# -------------------------------------------------------------------- banner

BANNER_TEXT = {
    "en": ("Stability of aquatic ecosystems under chemical pollution", "across a bioclimatic gradient"),
    "es": ("Estabilidad de los ecosistemas acuáticos ante la contaminación química",
           "a lo largo de un gradiente bioclimático"),
}


def draw_banner(sites, lang, out: Path):
    W, H = 1600, 400
    fig, ax = canvas(W, H, "#0b2b38")
    grad = np.linspace(0, 1, 512)[None, :]
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("b", ["#0a2733", "#0c3a4a", "#123f5e"])
    ax.imshow(grad, extent=(0, W, 0, H), cmap=cmap, aspect="auto", zorder=0)

    # faint response curves: a pulse, a dip, a recovery
    x = np.linspace(0, W, 800)
    rng = np.random.default_rng(7)
    for i in range(14):
        x0 = 780 + rng.uniform(-80, 260)
        depth = rng.uniform(40, 120)
        base = 40 + i * 24
        rec = rng.uniform(120, 360)
        y = base - depth * np.where(x > x0, (1 - np.exp(-(x - x0) / 22)) * np.exp(-(x - x0) / rec), 0)
        ax.plot(x, y, color="#9fdfe6", lw=1.2, alpha=0.05 + 0.012 * i, zorder=1)
    xx = np.linspace(1000, 1540, 400)
    yy = 268 - 120 * np.where(xx > 1080, (1 - np.exp(-(xx - 1080) / 18)) * np.exp(-(xx - 1080) / 170), 0)
    ax.plot(xx, yy, color="#7fe0e6", lw=4, alpha=0.9, zorder=2, solid_capstyle="round")
    for d in (1040, 1130, 1180, 1270, 1360, 1450, 1540):
        yd = np.interp(d, xx, yy)
        ax.add_patch(Circle((d, yd), 7, fc="#7fe0e6", ec="#0c3a4a", lw=2.5, zorder=3))
    ax.plot([1000, 1540], [268, 268], color="white", lw=1.5, alpha=0.35, zorder=2)

    # wordmark (letter-spaced) and tagline
    px, cx = 138, 92
    for ch in "STABLE":
        ax.text(cx, 236, ch, color="white", fontsize=pt(px), family=DISPLAY, weight="bold",
                ha="left", va="baseline", zorder=4)
        cx += text_w(ch, px, "bold", DISPLAY) + 14
    line1, line2 = BANNER_TEXT[lang]
    ax.text(96, 160, line1, color="white", alpha=0.92, fontsize=pt(33), weight="medium",
            ha="left", va="center", zorder=4)
    ax.text(96, 112, line2, color="#bfe3e8", fontsize=pt(26), ha="left", va="center", zorder=4)
    cx = 96 + text_w(line2, 26) + 30
    regions = sorted((s for s in sites if s.get("gradient")), key=lambda s: int(s["gradient"]))
    for s in regions:
        ax.add_patch(Circle((cx + 8, 112), 8, fc=s["color"], lw=0, zorder=4))
        label = s[f"country_{lang}"]
        ax.text(cx + 24, 112, label, color="#bfe3e8", fontsize=pt(26), ha="left", va="center", zorder=4)
        cx += 24 + text_w(label, 26) + 30
    fig.savefig(out, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    rounded(out, radius=20)


def draw_funders(out: Path) -> None:
    """Funder logos (official files in logos/) side by side on a white card."""
    files = [HERE / "logos" / "miciu-ue-aei.png", HERE / "logos" / "fundacion-bbva.png"]
    logos = [Image.open(f).convert("RGBA") for f in files if f.exists()]
    if not logos:
        return
    heights = [172, 100][: len(logos)]
    logos = [im.resize((round(im.width * h / im.height), h), Image.LANCZOS) for im, h in zip(logos, heights)]
    pad_x, pad_y, gap = 48, 38, 96
    W = 2 * pad_x + sum(im.width for im in logos) + gap * (len(logos) - 1)
    H = 2 * pad_y + max(im.height for im in logos)
    card = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    x = pad_x
    for im in logos:
        card.alpha_composite(im, (x, (H - im.height) // 2))
        x += im.width + gap
    card.save(out)
    rounded(out, radius=16)


# ------------------------------------------------------------ geojson/readme


def write_geojson(sites, team, out: Path) -> None:
    features = []
    for s in sites:
        members = [p for p in team if p["site"] == s["id"]]
        props = {"marker-color": s["color"], "marker-size": "large",
                 "Institution": s["name_en"].replace("|", " ")}
        if s.get("unit_en"):
            props["Unit"] = s["unit_en"]
        props.update({"City": s["city"], "Country": s["country_en"],
                      "Team": "; ".join(f"{p['name']} ({p['role_en']})" for p in members)})
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [float(s["lon"]), float(s["lat"])]},
                         "properties": props})
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def team_table(team, lang) -> str:
    """One row per person: portrait | name, role, affiliation, links and bio."""
    esc = html.escape
    rows = []
    for p in team:
        name = esc(p["name"])
        if p.get("github"):
            name = f'<a href="https://github.com/{esc(p["github"])}">{name}</a>'
        meta = f'{esc(p[f"role_{lang}"])} · {esc(p[f"affiliation_{lang}"])}'
        links = []
        if p.get("orcid"):
            links.append(f'<a href="https://orcid.org/{esc(p["orcid"])}">ORCID</a>')
        if p.get("website"):
            links.append(f'<a href="{esc(p["website"])}">Web</a>')
        if links:
            meta += " · " + " · ".join(links)
        rows.append("<tr>\n"
                    f'<td width="120" align="center" valign="top"><img src="{RAW}/img/team/{p["id"]}.png" '
                    f'width="96" alt="{esc(p["name"])}"></td>\n'
                    f'<td valign="top"><b>{name}</b><br><sub>{meta}</sub><br>{esc(p[f"bio_{lang}"])}</td>\n'
                    "</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def update_readme(path: Path, table: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"<!-- team:start -->.*?<!-- team:end -->",
                 lambda m: f"<!-- team:start -->\n{table}\n<!-- team:end -->", text, flags=re.S)
    path.write_text(new, encoding="utf-8")


# ---------------------------------------------------------------------- main


def main() -> None:
    sites, team = read_csv("institutions.csv"), read_csv("team.csv")
    colors = {s["id"]: s["color"] for s in sites}
    (IMG / "team").mkdir(parents=True, exist_ok=True)
    print("portraits")
    portraits = {}
    for p in team:
        portraits[p["id"]] = portrait(p, colors[p["site"]])
        portraits[p["id"]].resize((240, 240), Image.LANCZOS).save(IMG / "team" / f"{p['id']}.png", optimize=True)
    print("maps")
    world = load_world()
    for lang in LANGS:
        for theme in THEMES:
            draw_map(sites, team, portraits, theme, lang, world, IMG / f"map-{lang}-{theme}.png")
            draw_stability(theme, lang, IMG / f"stability-{lang}-{theme}.png")
        draw_banner(sites, lang, IMG / f"banner-{lang}.png")
    draw_funders(IMG / "funders.png")
    write_geojson(sites, team, PROFILE / "team-map.geojson")
    for lang, name in (("en", "README.md"), ("es", "README.es.md")):
        update_readme(PROFILE / name, team_table(team, lang))
    print("done")


if __name__ == "__main__":
    main()
