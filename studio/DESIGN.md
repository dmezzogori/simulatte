---
name: Simulatte Studio
description: A local workbench for simulation experiments, where every result reads as a row of a forest plot.
colors:
  paper: "#ffffff"
  ink: "#1b1d1f"
  ink-2: "#3f454b"
  muted: "#565d65"
  line: "#d8dce0"
  line-soft: "#eceef0"
  wash: "#f5f6f7"
  control-stroke: "#8c939a"
  axis-grey: "#9aa1a8"
  rail: "#13293d"
  rail-raised: "#20405c"
  rail-ink: "#f2f5f8"
  rail-muted: "#b3c1cf"
  evidence: "#0e5a9c"
  evidence-ink: "#0b4a80"
  evidence-tint: "#e8f0f8"
  own: "#6b3fa0"
  own-tint: "#f2ecf8"
  success-bg: "#e6f3eb"
  success-ink: "#1d6641"
  warn-bg: "#fdf2d8"
  warn-ink: "#714800"
  fail-bg: "#fbe8e5"
  fail-ink: "#a1261a"
  info-bg: "#eef1f4"
  info-ink: "#3f4c59"
typography:
  display:
    fontFamily: "Source Serif 4 Variable, Georgia, Times New Roman, serif"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Source Serif 4 Variable, Georgia, Times New Roman, serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.25
  brand:
    fontFamily: "Source Serif 4 Variable, Georgia, Times New Roman, serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.01em"
  figure:
    fontFamily: "Source Sans 3 Variable, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  estimate:
    fontFamily: "Source Sans 3 Variable, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 650
    fontFeature: "tnum"
  body:
    fontFamily: "Source Sans 3 Variable, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Source Sans 3 Variable, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
  tick:
    fontFamily: "Source Sans 3 Variable, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    fontFeature: "tnum"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
rounded:
  tag: "3px"
  control: "4px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
  control-height: "36px"
  rail-width: "248px"
  content-max: "1360px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "6px 14px"
    height: "36px"
  button-hover:
    backgroundColor: "{colors.wash}"
    textColor: "{colors.ink}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "6px 14px"
    height: "36px"
  button-disabled:
    backgroundColor: "{colors.wash}"
    textColor: "#6b7178"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
    height: "36px"
  segmented-on:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.tag}"
    padding: "5px 14px"
  nav-item:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.rail-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "7px 10px"
  nav-item-active:
    backgroundColor: "{colors.rail-raised}"
    textColor: "{colors.rail-ink}"
  badge-success:
    backgroundColor: "{colors.success-bg}"
    textColor: "{colors.success-ink}"
    rounded: "{rounded.tag}"
    padding: "2px 8px"
  badge-partial:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn-ink}"
    rounded: "{rounded.tag}"
    padding: "2px 8px"
  badge-failed:
    backgroundColor: "{colors.fail-bg}"
    textColor: "{colors.fail-ink}"
    rounded: "{rounded.tag}"
    padding: "2px 8px"
  badge-running:
    backgroundColor: "{colors.evidence-tint}"
    textColor: "{colors.evidence-ink}"
    rounded: "{rounded.tag}"
    padding: "2px 8px"
  badge-neutral:
    backgroundColor: "{colors.info-bg}"
    textColor: "{colors.info-ink}"
    rounded: "{rounded.tag}"
    padding: "2px 8px"
---

# Design System: Simulatte Studio

## Overview

**Creative North Star: "Forest Plot"**

Every result in Studio is a row of a forest plot: an estimate with its interval on a shared axis, and every comparison is a difference drawn against a line of zero. The work area is a white figure ground with ink text and hairline rules, the way a statistics paper sets a figure. Navigation lives apart from the figures on a deep ink-blue rail, so the white area holds only the results and the controls that change them.

Colour is spent on meaning, not decoration. Ink carries all data marks by default. Evidence blue appears only where a paired difference's interval excludes zero; violet appears only on the researcher's own layer (the chosen baseline); amber marks partial, degenerate or interval-less states; red is kept for failures. Sections are separated by a 1px ink top rule rather than boxed into cards, and the density is that of a reading page: 15px body text, tabular figures for every number, values printed at the precision their interval supports.

The world was established on the experiment results, cross-run comparison and saved-analysis pages and the project rail. The run page (recording player), the presentation editor and export still use legacy layouts: they inherit these tokens through aliases in `tokens.css` (`--teal`, `--tint`, `--blue`, `--amber`, `--radius`) but have not been redesigned yet. Legacy viewer styles in `studio/apps/viewer/src/style.css` style bare `header`, `nav` and `small` elements; inside `.project-workspace` those are reset to neutral with zero-specificity `:where()` rules, so new surfaces start from this system, not from the legacy viewer.

**Key Characteristics:**
- White figure ground, ink marks, hairline rules; an ink-blue rail carries navigation.
- Serif for page and block titles, sans with tabular figures for everything else.
- Squares are estimates, diamonds are differences, a vertical ink line is zero.
- Colour encodes evidence state plus one reserved accent for the researcher's own layer.
- Sections are ruled, not boxed. Flat surfaces; the only shadow belongs to the modal dialog.
- One motion: interval marks move in 160 ms; reduced motion shows the final state.

## Colors

A near-monochrome ink-on-white palette with one structural dark (the rail) and four semantic accents, each with a single job.

### Primary
- **Evidence Blue** (evidence): fills a difference diamond whose interval excludes zero; with **Deep Evidence Blue** (evidence-ink) for that difference's value text, diamond stroke and note. Also the focus outline and caret colour. **Evidence Wash** (evidence-tint) backs selected table rows and the running badge.

### Secondary
- **Researcher Violet** (own): the baseline configuration's label and its "Baseline" dot marker in figure and matrix. **Violet Wash** (own-tint) is defined for the same layer but not yet used on the shipped surfaces.

### Tertiary
- **Ink-Blue Rail** (rail): the navigation rail background. **Raised Rail** (rail-raised) marks the current page; a hover step (#1c3650) sits between them. **Rail Ink** (rail-ink) and **Rail Mist** (rail-muted) are the rail's text and secondary text (group names, counts, footer note).

### Neutral
- **Paper** (paper): the figure ground of every page, and the fill of a hollow (zero-including) difference diamond.
- **Ink** (ink): body text, titles, estimate squares, interval lines, the zero line, section top rules, primary buttons.
- **Graphite** (ink-2): interval brackets, convention lines, plain-language sentences under each block.
- **Slate** (muted): secondary text, KPI code names, axis tick labels, breadcrumbs.
- **Hairline** (line) and **Soft Hairline** (line-soft): rules under headers and between rows; row dividers inside figures use the soft one.
- **Wash** (wash): hover fill for buttons and table rows, the factor-slice bar, code blocks.
- **Control Stroke** (control-stroke): 1px border of buttons, inputs, selects and the segmented control.
- **Axis Grey** (axis-grey): axis baselines, tick marks, context ticks of configurations outside the focused slice, link underlines.

### State
- **Partial Amber** (warn-bg / warn-ink): partial results, "No interval" tags, empty populations, stale or connection notices, non-finite table cells.
- **Failure Red** (fail-bg / fail-ink): failed runs and replications (the struck-through replication mark), failure states and alerts.
- **Success Green** (success-bg / success-ink): completed or succeeded badges, the active-run bar, success toasts, the online dot.
- **Neutral State** (info-bg / info-ink): queued or waiting badges, measurement tags, dependency notices.

### Named Rules
**The Evidence Only Rule.** Blue fills a difference only when its interval excludes zero; a difference that includes zero is a hollow ink diamond. Colour never says "better" or "worse": differences carry a neutral sign and nothing else.

**The Own Layer Rule.** Violet belongs to what the researcher chose (the baseline). No data mark, status or link is ever violet.

**The Red Is Failure Rule.** Red appears only for failures. Partial, degenerate, missing or interval-less states are amber; neutral waiting states are grey.

## Typography

**Display Font:** Source Serif 4 Variable (with Georgia, Times New Roman)
**Body Font:** Source Sans 3 Variable (with -apple-system, Segoe UI, system-ui)
**Label/Mono Font:** ui-monospace (SFMono-Regular, Menlo) for KPI keys, output names and code

**Character:** A scholarly serif names things (the page, the KPI, the output), and a quiet humanist sans with tabular figures carries every number, label and sentence, the way a journal sets figure titles over a data table. Both faces are self-hosted variable fonts with optical sizing on.

### Hierarchy
- **Display** (serif 600, 32px, 1.15, -0.01em, balanced wrap): page titles (study, comparison, analysis).
- **Headline** (serif 600, 20px, 1.25): KPI block titles, analysis output titles, section headings (Replications, Annotations), dialog titles.
- **Brand** (serif 600, 24px, 1.1): the "Simulatte" wordmark at the top of the rail only.
- **Figure** (sans 600, 32px, 1.15, tabular): a single headline number in an analysis output, with its unit in 15px graphite beside it. Overview counters use the same face at 30px.
- **Estimate** (sans 650, 16px, tabular): the estimate in a figure row, followed by its `[lower, upper]` interval in graphite at body size.
- **Body** (sans 400, 15px, 1.5; paragraphs 1.55): running text; sentences under figures are capped at 75ch, page intros at 80ch.
- **Label** (sans 600, 14px): buttons, form labels, sub-headings such as "Difference from ..., paired by replication", table headers.
- **Tick** (sans 400, 13px, tabular): axis ticks, n counts, row notes, badges (650). The zero tick is ink 650.
- **Code** (mono 13px): KPI keys (`shopfloor/lateness`) and output names, always in Slate beside the human title.

### Named Rules
**The Tabular Figures Rule.** Every number that can sit above or beside another number (estimates, intervals, ticks, counts, table cells) uses tabular figures and a typographic minus (U+2212).

**The Supported Precision Rule.** Values print to the precision their interval supports (two significant digits of the half-width); floats without an interval read at four significant digits. Full digits appear only behind the explicit "Full digits" toggle.

**The Human Name First Rule.** A KPI or output shows a sentence-case human name in serif, with its code key beside it in mono Slate; the key is never dropped.

## Layout

A fixed rail (248px) on the left and a scrolling work area on the right. The work area has a 48px top bar (breadcrumbs left, connection state right, hairline below) and content padded 28px 40px 56px, centred at a 1360px maximum.

Results lay out as a responsive grid of KPI blocks, each at least 500px wide, gapped 36px vertically and 48px horizontally: two columns at 1280 and 1440. Inside a block, a fixed table layout holds three columns: label (34%), estimate text (31%) and the plot column, so axes align across rows and rows never move under live updates. Analysis headline numbers flow in a grid of at least 260px columns, gapped 24px 40px.

Spacing follows a 4px base (4, 8, 12, 16, 24, 32); larger section gaps are 28 to 40px. Controls are 36px tall.

Below 1100px the rail narrows to 210px and content padding drops to 24px. Below 850px the rail is 176px with counts hidden, every grid collapses to one column, and the label column widens to 40%.

## Elevation & Depth

The system is flat. Depth comes from the ink rail beside a white ground, from 1px rules and from the Wash fill on hover, never from shadows on content. The one exception is the modal dialog, which floats over a rail-tinted backdrop.

### Shadow Vocabulary
- **Dialog** (`box-shadow: 0 12px 32px rgba(19,41,61,.18)`, backdrop `rgba(19,41,61,.38)`): confirmation dialogs only.

### Named Rules
**The Ruled Not Boxed Rule.** A section starts with a 1px ink top rule (KPI blocks and output cards put the ink rule under their header); it is never a bordered, shadowed card. Hairlines (line, line-soft) divide inside a section.

## Shapes

Small, quiet corners: 4px on buttons, inputs, notices and dialogs; 3px on badges, tags and segmented options. Figures have no corners at all: estimate squares (10px), replication marks (9px squares), the 2px interval line, and differences as 12px squares rotated 45 degrees into diamonds. Round shapes are reserved for small indicator dots (the 8px baseline dot, the connection dot) and the spinner.

### Named Rules
**The Mark Grammar Rule.** Squares are estimates, diamonds are differences, a vertical ink line is zero. A degenerate estimate becomes a 3x16px bar, an insufficient one a dashed hollow square. Replication marks are countable squares: filled for done, half-filled for running, hollow grey for waiting, struck through in red for failed.

## Components

### Buttons
Plain, firm, and small: a white body with a dark stroke.
- **Shape:** gently squared corners (4px), 36px tall, padding 6px 14px, label type (14px, 600).
- **Default:** Paper fill, 1px Control Stroke border, Ink text.
- **Hover / Active / Focus:** hover fills Wash and darkens the border to Ink; active fills #e9ebed; focus shows a 2px Evidence Blue outline offset 2px. Background and border transition over 160 ms.
- **Primary:** Ink fill with white text; hover #363b40. Used for the single committing action of a form.
- **Disabled:** Wash fill, #c4c9ce border, #6b7178 text.
- **Inline:** 30px tall, padding 3px 10px, for actions inside rows and toasts.

### Segmented control (Figure / Matrix)
- A 1px Control Stroke frame with 2px padding; options are 14px 600 Graphite; the selected option is filled Ink with white text (3px corners). Keyboard focus outlines the option in Evidence Blue.

### Badges and tags
- **Style:** 3px corners, padding 2px 8px, 13px text weight 650, no border. Colour follows the state rules: green completed, amber partial or no interval (a completed run with missing replications shows amber), red failed or lost, blue-wash running, grey queued or neutral tags.

### Inputs / Fields
- **Style:** Paper fill, 1px Control Stroke, 4px corners, 36px tall, padding 6px 10px, 14px text. Labels sit above in 14px 600; hints in 14px Slate. In the results control line, fields sit inline with their label.
- **Focus / Hover:** hover darkens the border to Ink; focus uses the shared Evidence Blue outline.
- **Error:** border turns Failure Red (`aria-invalid`). Checkboxes and radios are 16px with Ink accent colour.

### Navigation (the rail)
- Ink-Blue Rail background, 20px 12px padding. The brand wordmark sits on top in serif, then the project name with a rule below it. Groups ("Studies", "Workspace") are titled in 14px 600 Rail Mist sentence case. Items are 15px 500, padding 7px 10px, 4px corners; hover #1c3650, current page Raised Rail at weight 650. The active study expands its children (Results, Analyses) indented to 26px at 14px. Counts sit right-aligned in Rail Mist tabular figures. Settings and a local-workspace note sit at the bottom.

### Tables
- Header cells in 14px 600 Graphite over a 1px Ink rule; body rows divided by Soft Hairline, the last by Hairline; hover Wash; selected rows Evidence Wash. Numeric cells right-aligned with tabular figures. No outer border and no zebra striping.

### Notices and states
- Notices are 4px-cornered panels with a tinted fill and matching 1px border: amber for warnings and stale or partial data, red for failures and alerts, grey for dependency and missing-data states, green for success toasts and the active-run bar. Empty states use Wash with a Hairline border.

### KPI block (signature component)
The forest-plot unit. A serif KPI title with its mono key on the right, over a 1px Ink rule. Configuration rows show the label (violet with a "Baseline" dot when chosen), countable replication marks, the estimate with its interval and an n note, and on a shared axis a square with its interval line. When a baseline is set, a "Difference from ..., paired by replication" sub-head opens the difference rows: each a diamond against a vertical zero line, filled Evidence Blue only when the interval excludes zero. The axis has Axis Grey ticks with 13px labels and an emphasised zero. One plain-language sentence closes the block. KPIs without an interval collect in a "Without intervals" list with an amber "No interval" tag. With more than eight configurations, one factor slice is shown full size and the others stay on each axis as grey context ticks; the Matrix view is a sortable table of the same estimates and differences.

Motion: interval lines, squares and diamonds move their position and width over 160 ms with an ease-out curve (`cubic-bezier(0.16, 1, 0.3, 1)`) when values update. Nothing else in the figure animates. Under `prefers-reduced-motion` all workspace transitions and animations are removed and the final state shows at once.

### Analysis output card
A serif output title with its kind and mono name on the right and an optional "Download CSV" button, over a 1px Ink rule. Single numbers read as a Figure-size value with unit; one-row summaries read as a row of label-value pairs (18px 600 tabular values); multi-row tables, interval tables (drawn as forest rows), distributions, images and HTML frames follow. A qualifier line (convention, population, grouping) sits under each output in 14px Graphite.

## Do's and Don'ts

### Do:
- **Do** draw every estimate as a square with its interval on a shared axis, and every difference as a diamond against a vertical zero line.
- **Do** fill a difference with Evidence Blue only when its interval excludes zero; leave it a hollow ink diamond otherwise.
- **Do** mark the researcher's baseline in Researcher Violet and use violet for nothing else.
- **Do** separate sections with a 1px ink rule and whitespace (28 to 40px).
- **Do** set page and block titles in Source Serif 4 and everything else in Source Sans 3 with tabular figures and a typographic minus.
- **Do** print values at the precision their interval supports and offer full digits only through an explicit toggle.
- **Do** keep a KPI's code key visible in mono Slate beside its human name.
- **Do** keep row order and column widths fixed while results update; only interval marks move, over 160 ms, and not at all under reduced motion.

### Don't:
- **Don't** colour a difference as better or worse; sign and evidence state are the only things a difference's colour may say.
- **Don't** use red for anything but failures; partial, degenerate and missing states are amber or grey.
- **Don't** box sections into bordered or shadowed cards; the only shadow is the modal dialog's.
- **Don't** put an uppercase or letter-spaced kicker above a title; section titles stand on their own.
- **Don't** show hashes or identifiers outside a provenance disclosure.
- **Don't** invent a unit the data does not carry.
