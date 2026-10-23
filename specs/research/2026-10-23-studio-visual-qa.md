# Studio manual visual QA — paused

Review: 2026-10-21. Paused and reconciled at Davide’s request on 2026-10-23.
Reviewer: Davide in Chrome on eva, accessing the installed-wheel Studio on bigboss through an SSH loopback tunnel. Safari was involved in the initial opening/authentication finding; this is not a full Safari acceptance run.

Branch `feature/studio-projects`, HEAD `736570c`; product/test head `098bfbd`. The wheel matched package source byte for byte when setup began; no rebuild was needed. This review records feedback and partial validation, **not complete acceptance, integration or release approval**. No product fixes or tests were changed. The initial QA brief reserved commits for the coordinator; on 2026-10-23 Davide explicitly overrode that restriction and instructed the guide to commit and push this QA evidence.

The full [QA1–QA42 observation log](2026-10-23-studio-visual-qa/observations.md) includes reproduction steps, spec/doc references, evidence and verification limits. Its live original is `.superpowers/sdd/2026-10-19-studio-p4-presentation/visual-qa.md`. The five supplied screenshots are copied beside the archive so the report does not rely on clipboard or temporary paths.

## Davide’s feedback requiring follow-up

Davide did not assign blocker / should fix / nit severities. The categories below describe the finding, not its priority. All remain unfixed in this review.

| ID | Finding | Evidence / interpretation |
| --- | --- | --- |
| QA3 | Opening a plain Studio URL in a new browser/session can show the old interface. | Reported after Safari → Chrome and reopening Safari. Code selects legacy App when session storage is absent or project-state detection fails; unauthenticated state endpoint returns a fresh-link instruction. Session loss is a code-supported cause; the exact pasted URL was not confirmed. A fresh one-time link worked. |
| QA9 | KPI-only individual run page does not expose its recorded KPIs. | Davide could not find them. Guide verified eight saved scalar KPIs, but the page has no separate numerical summary and `run.get` omits those scalar values. [Screenshot](2026-10-23-studio-visual-qa/screenshots/qa-9-kpi-only-run-missing-values.png). |
| QA33 | Plotly evidence fails in the presentation panel despite working in Analyses and exports. | “Renderer not recorded” is misleading: saved output descriptor contains the matching plotly.js 4.1.1 renderer. Panel reads it from `figure.json` instead of the descriptor. [Screenshot](2026-10-23-studio-visual-qa/screenshots/qa-33-presentation-plotly-renderer-not-recorded.png). |
| QA7 | Player section is visually cramped. | Davide: “the player section is a bit cramped visually, the UI needs polishing.” Run flow itself was accepted. |
| QA13 | Busy-slot feedback/actions appear below the fold. | Davide expected a top banner and had to scroll to fully see it. Top placement is his UX expectation; the spec does not explicitly mandate it. [Screenshot](2026-10-23-studio-visual-qa/screenshots/qa-12-busy-slot-message-below-fold.png). |
| QA14 | Busy-slot feedback stays stale after the owner finishes. | Davide waited on the page; no update indicated that the slot was free and the new run could be started. Explicit stop-and-run worked in a separate attempt. |
| QA18 | Analysis pages need substantial UI/UX work despite correct content. | Davide: “everything seems to be there and correct, it’s just very hard to understand.” He explicitly validated the listed population-analysis content and repeated the polishing concern for fleet utilization. This is broader than spacing; specific causes have not been isolated. |
| QA30 | Presentation editor also needs UI/UX polishing. | Davide accepted the guided editor save/reload flow and added “a bit of ui/ux polishing also here.” Specific controls not identified. |
| QA37 | Export preview is cramped inside a small dialog. | Davide needed both horizontal and vertical scrolling. Preview preparation and subsequent exports worked. |

## What passed

The following are Davide’s responses to the guided checks, except where explicitly marked as independently verified. Broad “all good” responses are not treated as a full accessibility audit or independent checks of every individual option.

- Demo overview/model navigation and selected existing-interpreter view.
- Production preparation/full-run flow; clear no-recording message for KPI-only run (the separate numerical-results gap is QA9).
- Busy-slot refusal and explicit replacement: Slow cancelled, Production succeeded. History retention and more involved ownership races were not separately confirmed.
- Experiment configuration counts, replication-level intervals, missing pairs, baseline differences and Bonferroni label.
- Analysis partial-input counts/statuses, warm-up/window metadata, overlapping/empty populations and distributions; saved Matplotlib/Seaborn/Plotly rendering and reload; post-run family-B subset execution; missing-field and incomplete-input refusals; same-snapshot rerun and unchanged refresh; fleet aggregation meanings.
- **Independently verified:** original-seed recording `cbe4e061896168a60d1eb9df936f56cf` is a succeeded verified replay of three-servers replication 0, seed 17. Request differs only in level; horizon 60 / warm-up 10 / plan `outcomes` retained. Trace integrity passed. New-seed recording `8300806d1fc0ba4a96b6837d4abf704a` succeeded as `new_seed`, seed 117, no verification, with only seed and level differing.
- Editor/evidence save/reload and direct presentation guided checks, except Plotly QA33 and measurement coverage QA28. General editor polishing remains open.
- PNG archive and first-frame inspection, MP4 playback and standaloneHTML opened from disk with the file tab offline, including all five evidence items/partial qualifiers. These are user checks; guide did not independently inspect the downloads on eva. In particular, export Plotly success does not resolve direct presentation QA33.
- Brief keyboard/focus and roughly 600px window passes reported no new problems; existing polish concerns remain.

## Other observations and verification corrections

- **QA1–QA2, guide observations:** Uvicorn deprecation warnings at launch and KeyboardInterrupt tracebacks on Ctrl-C in a PTY. Not Davide-assigned defects/severities.
- **QA10, guide error:** Slow fixture sleeps once. Delay 10 meant ~10 seconds, not 30; initial runs did not overlap. Repeated with a longer delay; busy handling was then observed and replacement accepted.
- **QA27:** Davide could not locate Open editor; prior instruction lacked its location. Potential discoverability concern, not established as a product defect.
- **QA28, fixture/guide error:** original demo records eight scalar KPIs but no series. The guide wrongly requested `shopfloor/finished`, and the initial preset selected that unsupported key. Synchronized measurement coverage must not be claimed. A separate series fixture is prepared, still unreviewed.
- **QA34–QA35:** export correctly refused a seven-column table with a long decimal. Saved presentation also used end 60, table limit 10 and Plotly 960×540, unlike earlier requested settings. The cause of that difference is unestablished. The refusal was not itself a product defect; the narrower excerpt allowed preview. [Refusal screenshot](2026-10-23-studio-visual-qa/screenshots/qa-34-export-table-width-refusal.png). Earlier broad save/reload acceptance does not establish the requested exact values.
- Eva connection failure was a stale tunnel after Studio restarted on a new random port (55940 → 59929), separate from QA3. Tunnel was corrected and verified at the time. Current process/tunnel state is not asserted at pause.

## Resume point and unreviewed work

**Resume with empty-project first run.** The last instructed command, run by Davide in a bigboss terminal, was:

```bash
/tmp/studio-qa/venv/bin/simulatte studio /tmp/studio-qa/first-run --port 8765 --no-browser
```

The eva forward for 8765 was configured; recheck it on resume and issue a fresh link. The folder currently has no authored files and no starter/application observation was received. Choose Production and inspect/apply the starter, then pause before environment preparation: use the local branch wheel, since published 0.17.0 is an older build with a different store schema.

Remaining manual coverage:

- Empty/no-argument/legacy opening and invalid-reference refusal; managed environment preparation/update/failure and recovery/reconstruction.
- Selected synchronized measurements in Studio, PNG/MP4 and offlineHTML. `/tmp/studio-qa/series-demo` has an actual `shopfloor/finished` series and a saved **Recorded measurements** presentation (`ff9c492c-f04c-454b-85ef-ab6843364140`); trace series presence was verified, UI/export behavior not reviewed.
- Changed-input refresh after later replications, the full eight-of-24 interruption/resume journey, and frozen presentation revision during a concurrent export.
- Separate comparison route, experiment authoring and fleet family/capability comparisons; detailed source-edit/auto-run/cross-tab/cancellation-failure ownership cases.
- User-facing storage/CLI/replay/prune/copy/retention journeys. Setup-only `store check` and prune dry-run succeeded earlier.
- Reduced motion, contrast, comprehensive keyboard/size checks and all empty/loading/error/reconnect states.
- Severity assignment, decisions on feedback, implementation and subsequent verification. No fixes were authorized by pausing or recording this feedback.

## Local artifacts and handoff

- `/tmp/studio-qa/demo`: populated project and Davide’s saved runs/analyses/presentation.
- `/tmp/studio-qa/venv` and `venv-plot`: fresh installed-wheel launcher/runtime.
- `/tmp/studio-qa/series-demo` and `series-state.json`: separate series fixture.
- `/tmp/studio-qa/replay-verification.json`, `new-seed-verification.json`, `prune-dry-run.json`: prior read-only/setup evidence.
- Eva SSH control socket `/tmp/studio-qa-bigboss-tunnel.sock`; historical forwards 59929 for demo and 8765 for first-run. Port/process state must be checked before reuse. The user-owned Studio processes/tunnels were not stopped on pause.
- Cortex session id for continuation: `44c7361c-b4c1-464a-8aa2-91b176de4015`.

Temporary projects are useful resumption state, not durable substitutes for this report and screenshot archive. Evidence is scoped to the reviewed branch and recorded dates.
