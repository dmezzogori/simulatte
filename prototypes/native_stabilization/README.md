# Representative native models and persistent Studio sessions

Stabilization starts at bdeb5cf003b8a6bc6b5902bef59ce4f2457b3b4f. Native remains
experimental v3 research. No release, push, portable baseline or new modeling
capability is part of this gate.

The three workload families are a balanced three-stage flow line, a heterogeneous
five-server mixed-route job shop and bursty arrivals with feedback-driven
admission. Specifications are fixed functions of seed/index; control uses lifetime
admitted/completed counts independently of warmup/reporting. Python production
actors are the oracle. FIFO/SPT, full/bounded, multiple seeds/warmups and budgets
must preserve their existing physical, reporting and identity contracts.

The long gate runs real installed Studio servers and browser clients for at least
15 minutes per host. It cycles completed/cancelled/failed attempts, samples host
RSS/descriptors and browser heap, closes/reconnects actual WebSockets, reloads,
seeks and resumes following. Store growth is recorded separately from retained
runtime/browser memory. Wall pacing keeps native progress observable and is not a
native execution performance benchmark. Credentials stay in private build files;
evidence must not contain bootstrap tokens, cookies or session secrets.

`prepare.py` creates a private environment from explicit core/native wheels and
copies the model project outside the checkout. `environment.py` records the
installed Python sources, binaries, assets' server identity and workload hashes.
Use a separate copy of that project for `gate.py`: it deletes its two source files
before reproducing twelve captured requests. `trajectory.py` needs its sibling
`workloads.py` on the import path, so run it with the private Python without `-I`.
The other installed probes can run with `-I`.

`endurance.py` creates another independent project and compares eighteen managed
requests at horizon 16,384 (64 times the matrix horizon), with up to 32,768 admitted
jobs per request. Both native retention modes must match the Python actors' final
control state and KPIs. These runs overlap browser activity; their elapsed times
are deliberately not used for speed comparisons.

`serve.py` starts the real packaged Studio server using its official WebSocket
configuration. On a remote host, forward its loopback port to the same local port
to preserve Host/Origin validation. `soak.mjs` takes `--targets <private.json>
--seconds 900 --engines chromium,firefox,webkit`; targets contain `name`, `access`
(the private server access-file path), and `output`. All six browser clients run
on bigboss, three against each server. Reconnection closes a real WebSocket with
an allowed application close code; fractional simulation seeks are rounded to an
integer to avoid browser range-input serialization differences.

Server RSS/descriptors and children are sampled every five seconds, browser DOM
and Chromium heap every fifteen seconds. `browser_resources.py` additionally
samples discoverable browser process trees during the latter part of the soak;
summed RSS can count shared pages more than once and omit reparented helpers.
It is a diagnostic rather than an accounting of browser memory. Chromium
heap is measured after explicit garbage collection. Firefox/WebKit do not provide
that same heap measurement through this harness.

After closing the clients and server, `audit.py` checks the timed attempts' traces,
publication outcomes and retention counters, deletes project source, and
reproduces one request for every case/retention pair. `analyze.py` reconciles both
hosts and writes the summary and evidence SHA-256 certificate. The research report
in `specs/research/2026-11-04-native-stabilization.md` states the results and limits.

`settled.mjs` verifies all charts on the actual committed result after updates
stop. `transport_probe.py` checks the hypothesis that a source resolved while
live breaks when read after successful publication; that hypothesis did not
reproduce on either host. The Linux timed browser-error gate remains failed.
`summary.json` distinguishes a completed study and passing model/reproduction
gates from this failed browser gate; validating the evidence does not turn it green.
