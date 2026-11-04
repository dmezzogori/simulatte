# Static heterogeneous native server capacities

Roadmap step 3 adds fixed parallel processing slots per server to the optional
experimental backend. Python's existing `Server(capacity=...)` is the oracle.
This is one modeling extension: no changing capacities, repeated route servers,
machine-specific speeds, failures, setup times or new release policy.

The public API accepts a copied positive-integer `capacities` sequence, one entry
per server; omission means all ones. Each count and their sum are below
UINT32_MAX. FIFO/SPT, unlimited/ConWIP release, right-open advancement, full and
bounded retention, stock KPIs and managed reproduction retain their contracts.
Shop utilization divides occupied slot-time by total available slot-time.
Like the production SimPy resource, each arrival/release trigger grants at most
one request. Simultaneous completions must preserve that scheduling order.

Unit-capacity encodings and schemas remain unchanged. Non-unit configurations
use NSTA0002 / NBST0002 (an added u32 capacity before each server record), the
NRST0002 bounded wrapper and separate managed capacity schemas. Capacities must
affect trajectory/control and reproduction identity even before any job arrives.
Provider projection/composition envelope versions stay unchanged; their nested
codec identifies the new serialization. The companion advertises additive
CAPACITY_VERSION=1 without making old API-2 providers unusable for unit models.

Validation: exact production grants, starts/ends, releases/completions and KPIs
across mixed capacities, ties, ConWIP, warmup and right-open boundaries; legacy
unit oracles; dynamic bounded admission/storage; provider integrity and clean
installs; source-deleted Coordinator reproduction; macOS/Linux native builds and
sanitizer/fault checks. This remains v3 research, with no portable-wheel deployment
baseline or Studio integration in this step.
