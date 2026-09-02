# The Simulatte identity

Internal reference. This is deliberately **not** part of the documentation site —
it describes how the marks are made, which is a maintainer concern, not something
a user of the library needs.

`build_identity.py` is the specification. The tables in it — `KERNING` and
`GEOMETRY` — *are* the identity. This file explains them. If the two ever
disagree, the script wins.

## Regenerating

```bash
uv run python scripts/brand/build_identity.py --download   # first run
uv run python scripts/brand/build_identity.py              # thereafter
uv run python scripts/brand/build_identity.py --check      # metrics only
uv run python scripts/brand/build_identity.py --proof /tmp/proof   # PNG proof sheets
```

Everything in `docs/assets/brand/`, plus `overrides/.icons/simulatte/mark.svg`
and `docs/assets/logo.png`, is output. **Do not hand-edit any of it** — the next
run will overwrite your changes.

The font is fetched into `scripts/brand/.fonts/`, which is gitignored. It is not
committed and not redistributed.

## The mark

The name set in **JetBrains Mono 500, lowercase**, at a shell prompt: a `>`, the
word, and a block cursor. The `u` doubles as a cup — three curls of steam above
it, a saucer beneath. The icon is that `u` with the `>` parked against it as a
handle.

Lowercase is the whole argument. It is what a command looks like.

## Why a generator instead of SVG files

Two reasons.

**Fonts cannot be relied on.** An SVG containing `<text font-family="JetBrains
Mono">` renders correctly only where that font is installed. Everywhere else it
silently falls back and the logo is wrong — wrong widths, wrong weight, and the
steam no longer sits over the `u` because the `u` moved. So the letters are
converted to outlines: the shapes are in the file, and no font is needed to draw
them.

**The numbers must live in one place.** Change `handle_height` and all eleven
assets update consistently. Hand-edited SVGs drift, and nobody can review a wall
of path coordinates.

## How it works

1. Load JetBrains Mono's variable font and instantiate it at `wght=500` for the
   word, `wght=400` for the tagline. Instancing the variable font — rather than
   using the static "Medium" file — guarantees the same weight the design source
   used.
2. Read the metrics from the font binary: x-height, cap height, ascender, one
   advance, and the **ink bounding box** of every glyph.
3. Lay the word out (below).
4. Ask fontTools for each glyph's outline and translate it into position.
5. Write the SVGs, then rasterise the PNGs.

All geometry is expressed at a notional font size of 100. At JetBrains Mono's
1000 units/em that makes the x-height exactly 55 and one advance exactly 60.

## Spacing

A monospaced font gives every glyph the same advance. That is right for code and
wrong for a logotype — the `i` and the `l` float in a cell they cannot fill while
the `m` is jammed against its neighbours. So the advances are discarded and the
line is rebuilt from ink:

1. Measure every glyph's ink bounds.
2. Compute each adjacent pair's natural ink gap, and take the **third smallest**
   as the target. Third, not smallest: the tightest pairs are outliers and
   matching them crushes the line.
3. Set every pair to that gap.
4. Apply `KERNING`, a per-pair correction in units of x-height, for what even ink
   gaps still get wrong — stem against stem needs air, curve against curve needs
   less. Positive closes a pair, negative opens it.

The two outer gaps — after the `>` and before the cursor — are **one value used
twice** (`side_gap`). They are equal by construction and cannot drift apart.

## The tagline

Set in JetBrains Mono 400, uppercase, and **tracked to fit**: the size is chosen
and the letter-spacing is then solved so the tagline's ink spans exactly the
mark's ink, flush under the `>` and under the cursor. The first glyph's left
bearing and the last glyph's right bearing are subtracted out, so what aligns is
the ink, not the advance box. At the current size the solved tracking is about
0.215 em. Change the kerning and the tagline re-fits itself.

## The steam

One S-wave, with its control points at exactly a third and two thirds of its
height and its amplitude mirrored about the stem. The outer two curls are that
same path translated sideways — congruent, so they are parallel, equidistant at
every height, and of identical curvature by construction rather than by eye.

Terminals are **round**. JetBrains Mono cuts its stems square but softens every
join and terminal; flat caps on the steam fight that.

## Colour

| Role | Dark ground | Light ground |
| --- | --- | --- |
| Ground | `#07090A` | `#F6F8F3` |
| Ink | `#E9F2EA` | `#101509` |
| Accent — steam and cursor | `#3DFB86` | `#0B6B33` |

The prompt is ink at **42%**. It is punctuation, not a letter. In one-colour
reproduction it goes to full strength or it goes away.

The accent is used for two things only: the steam and the cursor. Never the
wordmark itself.

## The files

| File | Use |
| --- | --- |
| `logo-on-dark.svg` / `logo-on-light.svg` | Wordmark |
| `logo-tagline-on-dark.svg` / `-on-light.svg` | Wordmark with tagline — the home page hero |
| `mark.svg` | Handled cup in `currentColor`, for **inlining**. This is the header logo |
| `mark-plain.svg` | Cup without the handle, `currentColor`. Only for 16 px |
| `mark-on-dark.svg` / `mark-on-light.svg` | Handled cup, square and padded, for `<img>` and avatars |
| `favicon.svg` | Handled cup, colouring itself via `prefers-color-scheme` |
| `icon-512/192/180/32.png` | PWA manifest, Apple touch icon, legacy favicon |
| `icon-16.png` | The plain cup — the handle does not resolve at 16 px |
| `social-card.png` | 1200×630 `og:image` |
| `social-card.svg` | Vector source for the above |

`docs/assets/logo.png` is also regenerated. **Its path must not change.** Every
already-published PyPI release renders its README against
`raw.githubusercontent.com/.../main/docs/assets/logo.png`; deleting it would
permanently break the images on those pages, whose stored HTML cannot be
re-rendered. Keeping the path and replacing the bytes makes them show the new
mark instead.

## How the header logo works

The theme's `partials/logo.html` emits an `<img>` when `theme.logo` is set. An
SVG inside an `<img>` is an isolated document: it cannot see `currentColor` or
the `data-md-color-scheme` attribute the palette toggle sets, so it would be the
wrong colour in one of the two schemes.

So `theme.logo` is **not** set. Instead `theme.icon.logo = "simulatte/mark"`
makes the theme `{% include %}` the SVG from `overrides/.icons/simulatte/`,
inlining it — where `currentColor` resolves and the accent comes from
`--simulatte-accent`, defined per scheme in `docs/assets/stylesheets/brand.css`.

## Minimum sizes

Wordmark: no smaller than **200 px** wide, or 34 mm in print. Below that, drop
the prompt and cursor rather than shrinking them.

Icon: no smaller than **16 px**, and use `mark-plain` there.

## Font licence

JetBrains Mono is © JetBrains s.r.o., licensed under the
[SIL Open Font License 1.1](https://github.com/JetBrains/JetBrainsMono/blob/master/OFL.txt).
The letterforms in these marks are outlines derived from it. The OFL's Reserved
Font Name clause governs redistributing modified *fonts*; no font file is
committed or redistributed here.
