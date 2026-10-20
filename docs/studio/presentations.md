# Presentations

A presentation is a named, saved view of one full recording together with the
evidence that supports it. It belongs to the project: the recording stays
unchanged, the presentation records how to look at it, and any number of
presentations or exports can share one recording. This page follows the path from a
numerical study to a presentation; the example project in `examples/studio_project`
is enough to try every step.

The path has five steps:

1. Run an [experiment](experiments.md) and a [population analysis](analysis.md).
2. Record a full run of the configuration and seed you want to show.
3. Save a presentation over that recording: framing, visibility, time range,
   measurements, annotations and evidence.
4. Present it in Studio.
5. Export a video or a standalone offline HTML from the same saved revision.

## An experiment is numerical; a presentation needs a full recording

Experiment replications are usually recorded at the KPI level. They keep scalar
results and datasets but no trace, so there is nothing to animate. To present one,
record a separate full run from it.

On **Presentations** (`#/presentations`), the card **Record a run for presentation**
asks for an **Original run** (any completed run with a result, including a committed
replication) and a **Seed**. **Preview differences** then shows what the new run
will be before anything starts, and **Record** starts it.

The recording clones the original's captured execution specification: model
source, physical layout, input data, parameters, stopping policy with its horizon and
warm-up (or finite-population conditions), measurement selection and runtime
requirements. It changes the seed and sets the recording level to `full`. Current
model defaults never fill anything in. An experiment run with horizon 10,000 and
warm-up 1,000 keeps both in its presentation recording even when the model's own
defaults are 1,000 and zero.

The seed decides what the result is called:

| Seed | Mode | Label shown | Meaning |
| --- | --- | --- | --- |
| The original's seed | `replay` | `verified replay`, or `replay, not verified` | The established replay path. After the run, its trace, semantic digest, KPI scalars, resolved runtime, dependencies and physical layout are compared with the original. A mismatch names the first failing check. |
| Any other seed | `new_seed` | `new recording, not a verified reproduction` | A new run of the same specification. It is never presented as a reproduction of a different replication and is never compared with the original. |
| Explicit edits (API) | `edited` | `edited run, distinct from the original` | Changed `parameters`, `stopping`, `measurements` or `kpis`, validated against the captured model metadata. Never verified. |

The diff table lists every field that differs (`field`, original value, presentation
value). With only the original seed, the preview says the run is identical apart from
the recording level. The Studio form changes the seed only; edits of parameters,
stopping policy, measurements or KPI keys are available through the
`presentation.diff` and `presentation.record` commands (`overrides`) and appear in
the same table. Edits of source, inputs, layout or runtime are a new model
preparation and are refused here; prepare the model again and run it normally. A
recording whose original ran on a different Simulatte runtime than the installed one
is refused; a historical environment is never silently substituted.

A recording needs an idle compatible slot (the original's worker, or the worker you
name). If every slot is busy, record again when one is free. Recording is not
available in a read-only session. A recording is held by a durable reference to its
own result and to the original's result until a presentation adopts it; see
[Storage and portability](storage.md#presentation-recordings) for pruning
unadopted recordings.

The `replay` command-line verb is the headless counterpart for the verified path:
`simulatte replay REQUEST_ID` (see the [CLI reference](cli.md#replay)).

## Create and save a presentation

Under **New presentation from a recording**, choose **Open editor →** next to an
interactive run or a succeeded presentation recording. The editor shows the recording
on a stage and the settings in a side column. Nothing you do on the stage (panning,
zooming, playing, seeking) changes the saved view. Only **Use current view** and
**Save presentation** do.

| Setting | What it saves |
| --- | --- |
| Name, Notes | A name (1 to 200 characters) and plain-text notes (up to 4,000 characters). |
| Saved framing | **Use current view** captures the visible world rectangle of the stage and the playback rate. **Reset to saved framing** returns to it; **Fit scene** is a temporary view. Saving without a captured framing takes the current view. |
| Time and rate | Start and end simulation time, inside the recording's finite range (the editor shows the range), and the playback rate. |
| Measurements | Up to eight recorded series, shown as single-run values. |
| Visibility and labels | Every entity and the default label rule, or explicit lists of entities to show and entities to label. |
| Annotations | Up to 50 plain-text notes of up to 2,000 characters, each optionally attached to a simulation time. |
| Evidence | Up to 12 items from saved analyses (below). |

Saving checks the whole manifest against the store: the recording must still have its
saved trace (its SHA-256 is part of the presentation), the time range must lie inside
the recording, and every evidence item must still match its analysis output. A refusal
names the field. Unknown keys and non-finite numbers are rejected; text is always
plain text.

The first save creates revision 1. Every later **Save new revision** creates the next
revision and never alters an earlier one: revisions are immutable. If another tab
saved a newer revision first, Studio says so and asks you to reload before applying
your changes again. Deleting a presentation asks for confirmation and does not delete
the recording or any analysis; see [Storage and portability](storage.md#explicit-deletion).

## Evidence

Evidence is explicit and bounded. Under **Evidence**, **Add evidence** opens a picker:

1. Choose a **Saved analysis**. Only succeeded executions are listed, with their
   available and planned replication counts and whether they are partial.
2. Choose an **Output**: a number, a table, a distribution, a figure or an image.
3. For a table, pick the first row, up to 50 rows and up to 12 columns. For a figure,
   pick the static view size (320 to 3840 px wide, 180 to 2160 px high).
4. **Add evidence**.

The qualifiers are copied from the analysis when you select the item and cannot be
edited: name, unit, population, planned and available replications and whether the
snapshot is partial, the inferential convention, and the source analysis name,
snapshot hash and execution id. An item is also bound to the exact output by a
content hash; if that output changes, the presentation refuses to save or export it
and asks you to select it again. A partial analysis stays visibly partial in every
target; later replications never join it.

Evidence bytes are served from the saved analysis. They are not copied into the
presentation record. A presentation revision and each export hold references to the
recording and to the analysis executions and snapshots they use, so these cannot be
pruned or deleted while the presentation exists.

## Present in Studio

**Present →** (on the editor and the list) opens presentation mode: a clean view with
the stage, the selected measurements, the playback controls and the notes, and a fixed
evidence panel on the right. Single-run measurements are labelled as such and kept
apart from the cross-replication findings in the panel, each of which carries its
qualifiers and an **Open analysis →** link to the full analysis. Playback starts at
the saved start time and rate and stays inside the saved interval. Panning and zooming
remain available and are temporary.

**Exit presentation** (or Escape) returns to the presentation editor and puts focus
back on the control you came from. Presentation mode is read-only; it works in a
read-only session.

## Export

**Export…** on a presentation opens the export dialog for that saved revision. The
revision and its evidence references are frozen when the export is admitted, so editing
the presentation while an export runs never changes it.

| Target | Format | Notes |
| --- | --- | --- |
| Video | MP4 (H.264), WebM (VP9 or VP8) | Chosen with the browser's encoder support. |
| Video | PNG frames | A ZIP64 archive with numbered images and `timing.json`; needs no video encoder. |
| Standalone HTML | One `.html` file | The recording, the saved view and the evidence, playable offline. |

Choose the format, then **Prepare preview**. Preparation validates every selected
evidence item against the saved analysis, builds the verified static views, checks the
target limits and draws a preview of the first frame at the actual target size with
the actual panel, text and static substitutions. Only then can the export start.
Start, finish and failure are recorded as an export job; an interrupted job (for
example the browser closing or Studio stopping) is marked `lost` the next time the
project is opened.

Video options are the interval, simulation units per second, 24, 30 or 60 fps, even
width and height up to 3840 x 2160, background, time overlay and **Evidence panel**
(on by default).

### What each target contains

All three targets use the same saved revision. The differences are fixed:

| Presentation content | Studio | Video | Standalone HTML |
| --- | --- | --- | --- |
| Camera, entity visibility and labels | Restores the saved framing and selections; temporary viewing changes are allowed. | Renders the saved framing, visibility and labels at the chosen output aspect ratio, letterboxed inside the animation area, never cropped. | Opens at the same view; navigation is allowed and **Reset view** returns to the saved one. |
| Time range and playback rate | Starts from the saved settings; playback controls stay available. | Encodes exactly the selected interval at the saved rate, with the established frame and end-state rules. | Opens at the saved start and rate; pause, seek and replay stay within the saved interval. |
| Selected recorded measurements | Shows the selected series' values at the current time, apart from the cross-replication evidence. | Renders the selected values synchronized to simulation time in the panel. | Includes the same selections and synchronized values offline. |
| Annotations and evidence identity | Shows the text and links to the frozen analyses. | Lists the text and the evidence qualifiers (units, population, planned and available replications, partial snapshot, convention, source and snapshot prefix) in the panel. | Includes the text, the qualifiers and the local evidence references; the original project is not needed. |
| Numerical evidence, table excerpts and figures | Shows saved outputs with the supported interaction and links to the full analyses. | A bounded static panel beside the animation; figures use a saved static view. | Bundles bounded numbers and table excerpts and static figure views as PNG or SVG. |
| Full analysis workspace and recomputation | Available by navigating to the analysis. | Outside the video. | Outside the file; no Python backend is required or offered. |

Both exports end with a plain-text provenance line: presentation id and revision,
recording attempt and a snapshot hash prefix. The snapshot hash is also checked
between the browser and the server at admission, so the preview you saw is the thing
that is exported.

### Limits and refusals

An export is refused, with the offending item named, when it cannot include all
selected content. Nothing is silently omitted, cropped or truncated.

| Limit | Value |
| --- | --- |
| Evidence items, annotations | 12 and 50 per presentation |
| Table excerpt | 50 rows and 12 columns; cell text must fit its column |
| Video frames | 100,000 (PNG archives: 10,000) |
| Video size | Even dimensions up to 3840 x 2160; at least 160 px of animation beside the panel (about 480 px wide) |
| Offline HTML recording | 100 MiB of trace |
| Offline HTML total | 150 MiB including statics, excerpts and the player |
| Static view | A readable PNG or SVG within the size bound |

The refusal states the reason and a remedy, for example: choose a smaller excerpt,
export at a taller size, turn the panel off, remove the item, or export a video when
the HTML is too large. A figure needs a static view: Studio renders a Plotly figure
locally to a PNG for this; an image output must be a saved PNG or SVG. If neither
exists, the export is refused until you remove the item or save a static image from
the analysis.

### Offline HTML

The HTML file is a single self-contained document that opens from `file://` without a
server or network. It embeds the complete original recording (including entity
labels, payloads and metadata), the saved presentation manifest, the verified static
views and the evidence excerpts, and it carries a content-security policy that allows
no network access and only `data:` images. Review what it contains before sharing: it
names the source analyses, their snapshot hashes and execution ids, and the layout.

The player opens the saved view and offers pause, seek, replay, the rate control,
**Reset view**, the evidence panel (**Evidence**) and the annotations; an annotation
with a time jumps to it. It checks the embedded metadata against the recording's
SHA-256 and refuses a mismatch. Older files that carry only a pinned layout keep
opening as before.

The [recording reference](../development/studio.md#share-a-recording) describes the
browser storage, codec and temporary-copy behavior that applies to every export.

## Limits you may meet

- A presentation needs a full recording. A KPI-only run offers **Replay at full
  level** (a verified replay) or a presentation recording instead.
- Evidence comes from succeeded analyses only. Re-run or refresh an analysis to get
  new numbers; an existing presentation keeps the evidence it selected.
- Presentation editing, recording and export are disabled in a read-only session
  (recovery view); presentation mode and reading are not.
- Presentations are not a slide or timeline authoring system. The evidence panel is
  fixed; custom-figure interaction exists only in Studio.
- This page does not claim results for any particular browser. Codec support
  depends on the browser, platform and resolution; the export dialog reports an
  unavailable codec and offers another format.
