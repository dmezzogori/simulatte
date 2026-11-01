# scripts/brand/build_identity.py
"""Generate the Simulatte visual identity from the JetBrains Mono outlines.

Every asset in ``docs/assets/brand/`` is produced by this script. Nothing there
is hand-drawn, and nothing there depends on a font being installed: the letters
are emitted as outlines, not as ``<text>``.

The constants in this file -- ``KERNING`` and ``GEOMETRY`` -- are the identity's
specification. ``scripts/brand/README.md`` explains them for humans; if the two
ever disagree, this file wins.

Usage::

    uv run python scripts/brand/build_identity.py --download
    uv run python scripts/brand/build_identity.py --check   # metrics only

Fonts are fetched into ``scripts/brand/.fonts/`` (gitignored) and are not
committed. JetBrains Mono is licensed under the SIL Open Font License 1.1.
"""

from __future__ import annotations

import argparse
import itertools
import math
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

if TYPE_CHECKING:
    from collections.abc import Iterable

# --------------------------------------------------------------------------- #
# The specification                                                            #
# --------------------------------------------------------------------------- #

WORD = "simulatte"
TAGLINE = "PYTHON DISCRETE EVENT SIMULATION"
PROMPT = ">"

#: Optical kerning corrections, in units of x-height. Applied on top of the
#: equal-ink-gap rhythm. Positive closes a pair, negative opens it.
KERNING: dict[str, float] = {
    "si": 0.010,
    "im": -0.020,
    "mu": -0.020,
    "ul": -0.020,
    "la": 0.000,
    "at": 0.010,
    "tt": -0.030,
    "te": 0.005,
}

#: Mark geometry. Every value is a ratio of the x-height except ``steam_pitch``,
#: which is a ratio of the u's ink width, and ``cursor_ratio``, which is a ratio
#: of the cursor's own height.
GEOMETRY: dict[str, float] = {
    "steam_height": 0.46,
    "steam_gap": 0.15,
    "steam_pitch": 0.30,
    "steam_throw": 0.075,
    "steam_weight": 0.095,
    "saucer_overhang": 0.14,
    "saucer_drop": 0.17,
    "saucer_weight": 0.115,
    "prompt_height": 1.00,
    "handle_height": 0.44,
    "handle_gap": 0.13,
    "handle_centre": 0.50,
    "cursor_ratio": 0.58,
    "side_gap": 0.62,
    "tagline_size": 0.47,
    "tagline_gap": 0.42,
    "pad_x": 0.55,
    "pad_top": 0.20,
    "pad_bottom": 0.34,
}

#: Palette. ``muted`` is the prompt, rendered as ink at 42% opacity.
PALETTES = {
    "dark": {"ink": "#E9F2EA", "accent": "#3DFB86", "ground": "#07090A"},
    "light": {"ink": "#101509", "accent": "#0B6B33", "ground": "#F6F8F3"},
}
PROMPT_OPACITY = 0.42

FONT_BASE = "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/fonts/variable/"
FONT_FILE = "JetBrainsMono[wght].ttf"

REPO = Path(__file__).resolve().parents[2]
FONT_DIR = Path(__file__).resolve().parent / ".fonts"
OUT_DIR = REPO / "docs" / "assets" / "brand"
LEGACY_LOGO = REPO / "docs" / "assets" / "logo.png"
#: The header logo is *inlined* by the theme (via ``theme.icon.logo``) so that it
#: inherits the palette; an ``<img>`` could not follow the light/dark toggle.
#: Written from the same geometry, so the two copies cannot drift.
THEME_ICON = REPO / "overrides" / ".icons" / "simulatte" / "mark.svg"

#: All geometry is expressed at this notional font size, mirroring the design
#: source. At upm 1000 this makes the x-height 55 and one advance 60.
EM = 100.0


# --------------------------------------------------------------------------- #
# Font access                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Ink:
    """The ink bounding box of one glyph, at ``EM``."""

    left: float
    right: float
    ascent: float
    descent: float
    advance: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.ascent + self.descent


class Face:
    """One weight of JetBrains Mono, measured and outlined at ``EM``."""

    def __init__(self, path: Path, weight: int) -> None:
        font = TTFont(path)
        if "fvar" in font:
            font = instantiateVariableFont(font, {"wght": weight}, inplace=False)
        self.font = font
        self.upm: int = font["head"].unitsPerEm
        self.scale = EM / self.upm
        self.glyphs = font.getGlyphSet()
        self.cmap = font.getBestCmap()
        self.hmtx = font["hmtx"]
        os2 = font["OS/2"]
        self.x_height = os2.sxHeight * self.scale
        self.cap_height = os2.sCapHeight * self.scale
        self.advance = self.hmtx[self.name_of("m")][0] * self.scale
        self.ascender = self.ink("l").ascent

    def name_of(self, char: str) -> str:
        try:
            return self.cmap[ord(char)]
        except KeyError as exc:  # pragma: no cover - defensive
            msg = f"{char!r} is not in the font"
            raise SystemExit(msg) from exc

    def ink(self, char: str) -> Ink:
        name = self.name_of(char)
        pen = BoundsPen(self.glyphs)
        self.glyphs[name].draw(pen)
        advance = self.hmtx[name][0] * self.scale
        if pen.bounds is None:  # a space, or another blank glyph
            return Ink(0.0, 0.0, 0.0, 0.0, advance)
        x0, y0, x1, y1 = (v * self.scale for v in pen.bounds)
        return Ink(x0, x1, y1, -y0, advance)

    def outline(self, char: str, pen_x: float, baseline: float, size: float = EM) -> str:
        """Path data for ``char``, placed with its pen at ``(pen_x, baseline)``."""
        k = size / self.upm
        pen = SVGPathPen(self.glyphs, ntos=fmt)
        self.glyphs[self.name_of(char)].draw(TransformPen(pen, Transform(k, 0, 0, -k, pen_x, baseline)))
        return pen.getCommands()


def fmt(value: float) -> str:
    """Round to two decimals and drop the noise, so diffs stay readable."""
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


# --------------------------------------------------------------------------- #
# Layout                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class Layout:
    """Where every element of the mark sits, at ``EM``."""

    pens: list[float]
    inks: list[Ink]
    gap: float
    word_start: float
    word_end: float
    u_left: float
    u_right: float
    u_centre: float
    baseline: float
    steam_base: float
    steam_top: float
    saucer_y: float
    saucer_half: float
    cursor: tuple[float, float, float, float]
    prompt_lockup: tuple[float, float, float]  # ink height, centre x, centre y
    prompt_handle: tuple[float, float, float]
    handle_ink: tuple[float, float]
    width: float
    height: float
    x_height: float
    advance: float
    ascender: float


def optical_pens(face: Face, word: str, x_height: float) -> tuple[list[float], list[Ink], float]:
    """Rebuild the line from ink, discarding the font's monospaced advances.

    Every pair is set to the same ink gap -- the third smallest natural gap, so
    that the tightest outliers do not crush the line -- and then corrected per
    pair from :data:`KERNING`.
    """
    inks = [face.ink(c) for c in word]
    advance = inks[0].advance
    natural = [(advance - inks[i - 1].right) + inks[i].left for i in range(1, len(word))]
    gap = sorted(natural)[2]
    pens = [0.0]
    for i in range(1, len(word)):
        tighten = KERNING.get(word[i - 1] + word[i], 0.0) * x_height
        pens.append(pens[i - 1] + inks[i - 1].right + gap - tighten - inks[i].left)
    return pens, inks, gap


def build_layout(face: Face) -> Layout:
    g = GEOMETRY
    xh = face.x_height
    pens, inks, gap = optical_pens(face, WORD, xh)
    prompt = face.ink(PROMPT)

    pad_x = g["pad_x"] * xh
    side_gap = g["side_gap"] * xh

    # The prompt, optically scaled so its ink height equals the x-height.
    p_scale = (g["prompt_height"] * xh) / prompt.height
    prompt_ink_w = prompt.width * p_scale

    word_start = pad_x + prompt_ink_w + side_gap
    offset = word_start - inks[0].left
    pens = [offset + p for p in pens]
    word_end = pens[-1] + inks[-1].right

    u_left = pens[3] + inks[3].left
    u_right = pens[3] + inks[3].right
    u_centre = (u_left + u_right) / 2
    u_width = inks[3].width

    pad_top = g["pad_top"] * xh
    steam_h = g["steam_height"] * xh
    steam_gap = g["steam_gap"] * xh
    baseline = pad_top + steam_h + steam_gap + xh
    steam_base = baseline - xh - steam_gap
    steam_top = steam_base - steam_h

    saucer_half = u_width / 2 + g["saucer_overhang"] * xh
    saucer_y = baseline + g["saucer_drop"] * xh
    saucer_w = g["saucer_weight"] * xh

    cursor_h = xh
    cursor_w = g["cursor_ratio"] * cursor_h
    cursor = (word_end + side_gap, baseline - xh, cursor_w, cursor_h)

    width = cursor[0] + cursor_w + pad_x
    height = saucer_y + saucer_w / 2 + g["pad_bottom"] * xh

    lockup = (g["prompt_height"] * xh, pad_x + prompt_ink_w / 2, baseline - xh / 2)

    h_scale = (g["handle_height"] * xh) / prompt.height
    handle_l = u_right + g["handle_gap"] * xh
    handle_w = prompt.width * h_scale
    handle = (g["handle_height"] * xh, handle_l + handle_w / 2, baseline - g["handle_centre"] * xh)

    return Layout(
        pens=pens,
        inks=inks,
        gap=gap,
        word_start=word_start,
        word_end=word_end,
        u_left=u_left,
        u_right=u_right,
        u_centre=u_centre,
        baseline=baseline,
        steam_base=steam_base,
        steam_top=steam_top,
        saucer_y=saucer_y,
        saucer_half=saucer_half,
        cursor=cursor,
        prompt_lockup=lockup,
        prompt_handle=handle,
        handle_ink=(handle_l, handle_l + handle_w),
        width=width,
        height=height,
        x_height=xh,
        advance=face.advance,
        ascender=face.ascender,
    )


# --------------------------------------------------------------------------- #
# Drawing                                                                      #
# --------------------------------------------------------------------------- #


def steam_path(x: float, base: float, height: float, throw: float) -> str:
    """One symmetric S-wave, control points at a third and two thirds.

    All three curls are this same path translated sideways, so they are
    congruent: parallel, equidistant at every height, uniform in curvature.
    """
    return (
        f"M{fmt(x)} {fmt(base)} "
        f"C{fmt(x - throw)} {fmt(base - height * 0.33)} "
        f"{fmt(x + throw)} {fmt(base - height * 0.67)} "
        f"{fmt(x)} {fmt(base - height)}"
    )


def steam(lay: Layout, colour: str) -> str:
    g = GEOMETRY
    pitch = g["steam_pitch"] * lay.inks[3].width
    throw = g["steam_throw"] * lay.x_height
    weight = g["steam_weight"] * lay.x_height
    paths = "".join(
        f'<path d="{steam_path(lay.u_centre + i * pitch, lay.steam_base, g["steam_height"] * lay.x_height, throw)}"/>'
        for i in (-1, 0, 1)
    )
    return f'<g fill="none" stroke="{colour}" stroke-width="{fmt(weight)}" stroke-linecap="round">{paths}</g>'


def saucer(lay: Layout, colour: str) -> str:
    weight = GEOMETRY["saucer_weight"] * lay.x_height
    return (
        f'<path fill="none" stroke="{colour}" stroke-width="{fmt(weight)}" stroke-linecap="round" '
        f'd="M{fmt(lay.u_centre - lay.saucer_half)} {fmt(lay.saucer_y)} '
        f'H{fmt(lay.u_centre + lay.saucer_half)}"/>'
    )


def prompt_glyph(face: Face, lay: Layout, spec: tuple[float, float, float], colour: str, opacity: float) -> str:
    """The ``>`` placed by ink centre and ink height, at any scale."""
    height, cx, cy = spec
    ink = face.ink(PROMPT)
    scale = height / ink.height
    pen_x = cx - (ink.left + ink.width / 2) * scale
    baseline = cy + (ink.ascent - ink.descent) * scale / 2
    op = f' fill-opacity="{opacity}"' if opacity < 1 else ""
    return f'<path fill="{colour}"{op} d="{face.outline(PROMPT, pen_x, baseline, EM * scale)}"/>'


def word_paths(face: Face, lay: Layout, colour: str) -> str:
    d = " ".join(face.outline(c, lay.pens[i], lay.baseline) for i, c in enumerate(WORD))
    return f'<path fill="{colour}" d="{d}"/>'


def cursor_rect(lay: Layout, colour: str) -> str:
    x, y, w, h = lay.cursor
    return f'<rect fill="{colour}" x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}"/>'


def tagline_paths(tag_face: Face, lay: Layout, colour: str) -> tuple[str, float, float]:
    """The tagline, tracked so its ink spans the mark exactly.

    Returns the markup, the baseline, and the resulting tracking in em -- the
    tracking is solved for, never chosen.
    """
    g = GEOMETRY
    size = g["tagline_size"] * lay.x_height
    k = size / EM
    first = tag_face.ink(TAGLINE[0])
    last = tag_face.ink(TAGLINE[-1])
    advance = first.advance * k

    bx0 = GEOMETRY["pad_x"] * lay.x_height
    bx1 = lay.cursor[0] + lay.cursor[2]
    n = len(TAGLINE)
    track = (bx1 - bx0 - last.right * k + first.left * k) / (n - 1) - advance

    pen0 = bx0 - first.left * k
    baseline = (
        lay.saucer_y + g["saucer_weight"] * lay.x_height / 2 + g["tagline_gap"] * lay.x_height + tag_face.cap_height * k
    )

    d = " ".join(
        tag_face.outline(ch, pen0 + i * (advance + track), baseline, size) for i, ch in enumerate(TAGLINE) if ch != " "
    )
    return f'<path fill="{colour}" d="{d}"/>', baseline, track / size


# --------------------------------------------------------------------------- #
# Assets                                                                       #
# --------------------------------------------------------------------------- #


def svg(view: Iterable[float], body: str, *, title: str, extra: str = "") -> str:
    vb = " ".join(fmt(v) for v in view)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" '
        f'aria-label="{title}">{extra}<title>{title}</title>{body}</svg>\n'
    )


def view_box(svg_text: str) -> re.Match[str]:
    """Locate an asset's viewBox. Absence is a bug in this script, not input."""
    match = re.search(r'viewBox="([^"]+)"', svg_text)
    if match is None:  # pragma: no cover - every asset this script emits has one
        msg = "asset has no viewBox"
        raise SystemExit(msg)
    return match


def refit(svg_text: str, aspect: float = 1.0, pad: float = 0.0) -> str:
    """Re-frame an asset to a given aspect, with breathing room.

    The mark's natural crop is portrait; app icons, touch icons and favicons are
    all square, so they are re-framed rather than squashed.
    """
    match = view_box(svg_text)
    x0, y0, w, h = (float(v) for v in match.group(1).split())
    if w / h < aspect:
        new_w = h * aspect
        x0 -= (new_w - w) / 2
        w = new_w
    else:
        new_h = w / aspect
        y0 -= (new_h - h) / 2
        h = new_h
    x0 -= w * pad
    y0 -= h * pad
    w *= 1 + 2 * pad
    h *= 1 + 2 * pad
    box = " ".join(fmt(v) for v in (x0, y0, w, h))
    return svg_text[: match.start()] + f'viewBox="{box}"' + svg_text[match.end() :]


def wordmark(face: Face, lay: Layout, ink: str, accent: str) -> str:
    return (
        prompt_glyph(face, lay, lay.prompt_lockup, ink, PROMPT_OPACITY)
        + word_paths(face, lay, ink)
        + steam(lay, accent)
        + saucer(lay, ink)
        + cursor_rect(lay, accent)
    )


def icon_body(face: Face, lay: Layout, ink: str, accent: str, *, handle: bool) -> tuple[str, list[float]]:
    margin = 0.12 * lay.x_height
    weight = GEOMETRY["saucer_weight"] * lay.x_height
    steam_w = GEOMETRY["steam_weight"] * lay.x_height
    x0 = lay.u_centre - lay.saucer_half - weight / 2 - margin
    x1 = (lay.handle_ink[1] if handle else lay.u_centre + lay.saucer_half + weight / 2) + margin
    y0 = lay.steam_top - steam_w / 2 - margin
    y1 = lay.saucer_y + weight / 2 + margin
    body = (
        f'<path fill="{ink}" d="{face.outline("u", lay.pens[3], lay.baseline)}"/>'
        + steam(lay, accent)
        + saucer(lay, ink)
    )
    if handle:
        body += prompt_glyph(face, lay, lay.prompt_handle, ink, 1.0)
    return body, [x0, y0, x1 - x0, y1 - y0]


def build_assets(face: Face, tag_face: Face, lay: Layout) -> dict[str, str]:
    out: dict[str, str] = {}
    view = [0.0, 0.0, lay.width, lay.height]

    for name, pal in PALETTES.items():
        ink, accent = pal["ink"], pal["accent"]
        out[f"logo-on-{name}.svg"] = svg(view, wordmark(face, lay, ink, accent), title="Simulatte")

        tag, baseline, _ = tagline_paths(tag_face, lay, ink)
        tag_view = [0.0, 0.0, lay.width, baseline + 0.30 * lay.x_height]
        out[f"logo-tagline-on-{name}.svg"] = svg(
            tag_view,
            wordmark(face, lay, ink, accent) + tag,
            title="Simulatte — Python discrete event simulation",
        )

        body, box = icon_body(face, lay, ink, accent, handle=True)
        # square + padded: these are used as <img>, as avatars and as app icons
        out[f"mark-on-{name}.svg"] = refit(svg(box, body, title="Simulatte"), 1.0, 0.06)

    # currentColor cuts, for inlining into a page that sets its own colours.
    body, box = icon_body(face, lay, "currentColor", "var(--simulatte-accent, currentColor)", handle=True)
    out["mark.svg"] = svg(box, body, title="Simulatte")
    body, box = icon_body(face, lay, "currentColor", "var(--simulatte-accent, currentColor)", handle=False)
    out["mark-plain.svg"] = svg(box, body, title="Simulatte")

    # Social card: the dark lockup, centred on a 1200x630 field.
    pal = PALETTES["dark"]
    tag, baseline, _ = tagline_paths(tag_face, lay, pal["ink"])
    content_h = baseline + 0.30 * lay.x_height
    card_w = lay.width / 0.70
    card_h = card_w * 630 / 1200
    out["social-card.svg"] = svg(
        [-(card_w - lay.width) / 2, -(card_h - content_h) / 2, card_w, card_h],
        f'<rect x="{fmt(-card_w)}" y="{fmt(-card_h)}" width="{fmt(card_w * 3)}" '
        f'height="{fmt(card_h * 3)}" fill="{pal["ground"]}"/>' + wordmark(face, lay, pal["ink"], pal["accent"]) + tag,
        title="Simulatte — Python discrete event simulation",
    )

    # A favicon has to colour itself: it is loaded in isolation.
    body, box = icon_body(face, lay, "var(--i)", "var(--a)", handle=True)
    style = (
        "<style>:root{--i:#101509;--a:#0B6B33}@media(prefers-color-scheme:dark){:root{--i:#E9F2EA;--a:#3DFB86}}</style>"
    )
    out["favicon.svg"] = refit(svg(box, body, title="Simulatte", extra=style), 1.0, 0.05)
    return out


# --------------------------------------------------------------------------- #
# Rasterising                                                                  #
# --------------------------------------------------------------------------- #

_TOKENS = re.compile(r"([MLCQZHVmlcqzhv])|(-?\d*\.?\d+)")


def parse_path(data: str) -> tuple[list[tuple[float, float]], list[int]]:
    """Parse the absolute path subset this script emits into matplotlib form."""
    from matplotlib.path import Path as MPath

    # matplotlib exposes these as numpy scalars; int() keeps the list homogeneous
    moveto, lineto = int(MPath.MOVETO), int(MPath.LINETO)
    curve3, curve4 = int(MPath.CURVE3), int(MPath.CURVE4)
    close = int(MPath.CLOSEPOLY)

    verts: list[tuple[float, float]] = []
    codes: list[int] = []
    nums: list[float] = []
    cmd = ""
    cur = (0.0, 0.0)
    start = (0.0, 0.0)

    def flush() -> None:
        nonlocal cur, start, nums
        if not cmd:
            nums = []
            return
        if cmd == "M":
            for i in range(0, len(nums), 2):
                cur = (nums[i], nums[i + 1])
                verts.append(cur)
                codes.append(moveto if i == 0 else lineto)
                if i == 0:
                    start = cur
        elif cmd == "L":
            for i in range(0, len(nums), 2):
                cur = (nums[i], nums[i + 1])
                verts.append(cur)
                codes.append(lineto)
        elif cmd == "H":
            for v in nums:
                cur = (v, cur[1])
                verts.append(cur)
                codes.append(lineto)
        elif cmd == "V":
            for v in nums:
                cur = (cur[0], v)
                verts.append(cur)
                codes.append(lineto)
        elif cmd == "C":
            for i in range(0, len(nums), 6):
                pts = [(nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3]), (nums[i + 4], nums[i + 5])]
                verts.extend(pts)
                codes.extend([curve4] * 3)
                cur = pts[-1]
        elif cmd == "Q":
            for i in range(0, len(nums), 4):
                pts = [(nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3])]
                verts.extend(pts)
                codes.extend([curve3] * 2)
                cur = pts[-1]
        elif cmd == "Z":
            verts.append(start)
            codes.append(close)
            cur = start
        nums = []

    for m in _TOKENS.finditer(data):
        if m.group(1):
            flush()
            cmd = m.group(1).upper()
            if cmd == "Z":
                flush()
                cmd = ""
        else:
            nums.append(float(m.group(2)))
    flush()
    return verts, codes


def rasterise(svg_text: str, path: Path, px: int, ground: str | None) -> None:
    """Render one of our own SVGs to PNG, so the output can be checked by eye."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch, Rectangle
    from matplotlib.path import Path as MPath

    x0, y0, w, h = (float(v) for v in view_box(svg_text).group(1).split())
    ratio = h / w
    # round to whole pixels: float drift here costs a row and breaks og:image
    fig = plt.figure(figsize=(px / 100, round(px * ratio) / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(x0, x0 + w)
    ax.set_ylim(y0 + h, y0)
    ax.axis("off")
    if ground:
        fig.patch.set_facecolor(ground)
        ax.add_patch(Rectangle((x0, y0), w, h, facecolor=ground, edgecolor="none", zorder=0))
    else:
        fig.patch.set_alpha(0)

    def resolve(colour: str) -> str:
        # currentColor and CSS vars have no meaning outside a document; the
        # proof renders them as ink so the shape is still checkable.
        if colour.startswith("var(") or colour == "currentColor":
            return PALETTES["dark" if ground == PALETTES["dark"]["ground"] else "light"]["ink"]
        return colour

    # user units -> points: pixels are px/w per unit, and a point is dpi/72 pixels
    to_points = (px / w) * 72 / 100
    attr = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
    stack: list[dict[str, str]] = [{}]
    # SVG paints in document order; matplotlib paints by zorder, so hand out
    # increasing zorders as elements are met rather than fixing them by kind.
    order = itertools.count(1)

    for tag in re.finditer(r"<(/?)(g|path|rect)\b([^>]*?)(/?)>", svg_text):
        closing, kind, raw, self_closing = tag.groups()
        if closing:
            if len(stack) > 1:
                stack.pop()
            continue
        own = dict(attr.findall(raw))
        merged = {**stack[-1], **own}
        if kind == "g":
            if not self_closing:
                stack.append(merged)
            continue

        fill = merged.get("fill", "#000")
        stroke = merged.get("stroke")
        alpha = float(merged.get("fill-opacity", 1.0))

        if kind == "rect":
            box = (float(merged["x"]), float(merged["y"]))
            ax.add_patch(
                Rectangle(
                    box,
                    float(merged["width"]),
                    float(merged["height"]),
                    facecolor=resolve(fill),
                    edgecolor="none",
                    alpha=alpha,
                    zorder=next(order),
                )
            )
            continue

        if "d" not in merged:
            continue
        verts, codes = parse_path(merged["d"])
        if not verts:
            continue
        mpath = MPath(verts, codes)
        if stroke and stroke != "none":
            ax.add_patch(
                PathPatch(
                    mpath,
                    facecolor="none",
                    edgecolor=resolve(stroke),
                    linewidth=float(merged.get("stroke-width", 1.0)) * to_points,
                    capstyle=merged.get("stroke-linecap", "butt"),
                    joinstyle="round",
                    zorder=next(order),
                )
            )
        if fill and fill != "none":
            ax.add_patch(PathPatch(mpath, facecolor=resolve(fill), edgecolor="none", alpha=alpha, zorder=next(order)))

    fig.savefig(path, dpi=100, transparent=ground is None)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #


def download_font() -> Path:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    target = FONT_DIR / FONT_FILE
    if not target.exists():
        url = FONT_BASE + FONT_FILE
        print(f"fetching {url}")
        with urllib.request.urlopen(url, timeout=60) as r, target.open("wb") as fh:
            fh.write(r.read())
    return target


def report(lay: Layout, tag_track: float) -> None:
    print("--- measured from JetBrains Mono, at font-size 100 ---")
    print(f"  x-height {lay.x_height:.1f}   ascender {lay.ascender:.1f}   advance {lay.advance:.1f}")
    print(f"  optical ink gap {lay.gap:.2f}   mark {lay.width:.1f} x {lay.height:.1f}")
    print(f"  side gap {GEOMETRY['side_gap'] * lay.x_height:.2f} (both sides, by construction)")
    print(f"  cursor {lay.cursor[2]:.2f} wide x {lay.cursor[3]:.2f} tall")
    print(f"  tagline tracking solved to {tag_track:.3f} em")
    ratio = lay.cursor[2] / lay.cursor[3]
    if not math.isclose(ratio, GEOMETRY["cursor_ratio"], rel_tol=1e-6):  # pragma: no cover
        msg = "cursor ratio drifted"
        raise SystemExit(msg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download", action="store_true", help="fetch the font if it is missing")
    ap.add_argument("--check", action="store_true", help="report metrics without writing anything")
    ap.add_argument("--proof", type=Path, default=None, help="write proof sheets to this directory")
    args = ap.parse_args(argv)

    font_path = FONT_DIR / FONT_FILE
    if not font_path.exists():
        if not args.download:
            print(f"font not found at {font_path} — re-run with --download", file=sys.stderr)
            return 2
        font_path = download_font()

    face = Face(font_path, 500)
    tag_face = Face(font_path, 400)
    lay = build_layout(face)
    _, _, track = tagline_paths(tag_face, lay, "#000")
    report(lay, track)

    if args.check:
        return 0

    assets = build_assets(face, tag_face, lay)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(assets.items()):
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        print(f"  wrote {name}  ({len(text)} bytes)")

    THEME_ICON.parent.mkdir(parents=True, exist_ok=True)
    THEME_ICON.write_text(assets["mark.svg"], encoding="utf-8")
    print(f"  wrote {THEME_ICON.relative_to(REPO)}")

    # Raster icons, from the dark cut so they read on any launcher background.
    icon = assets["mark-on-dark.svg"]
    for px in (512, 192, 180, 32):
        rasterise(icon, OUT_DIR / f"icon-{px}.png", px, PALETTES["dark"]["ground"])
        print(f"  wrote icon-{px}.png")
    rasterise(refit(assets["mark-plain.svg"], 1.0, 0.04), OUT_DIR / "icon-16.png", 16, PALETTES["dark"]["ground"])
    print("  wrote icon-16.png")

    rasterise(assets["social-card.svg"], OUT_DIR / "social-card.png", 1200, PALETTES["dark"]["ground"])
    print("  wrote social-card.png")

    # The legacy path keeps its name so already-published PyPI pages, whose
    # stored HTML points at main, show the new mark instead of a broken image.
    rasterise(assets["logo-on-dark.svg"], LEGACY_LOGO, 1200, PALETTES["dark"]["ground"])
    print(f"  replaced {LEGACY_LOGO.relative_to(REPO)}")

    if args.proof:
        args.proof.mkdir(parents=True, exist_ok=True)
        for name, text in assets.items():
            ground = PALETTES["light"]["ground"] if "light" in name else PALETTES["dark"]["ground"]
            rasterise(text, args.proof / f"{Path(name).stem}.png", 1000, ground)
        rasterise(assets["mark-on-dark.svg"], args.proof / "header-24px.png", 24, PALETTES["dark"]["ground"])
        rasterise(assets["mark-on-dark.svg"], args.proof / "favicon-16px.png", 16, PALETTES["dark"]["ground"])
        print(f"  proofs in {args.proof}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
