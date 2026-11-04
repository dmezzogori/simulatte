/* Retention owns recyclable storage; the engine owns event transitions. */
#include "retention.h"
#include "dynamic_engine_internal.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#ifdef ND_TEST_ALLOCATIONS
#define calloc nd_test_calloc
#define realloc nd_test_realloc
#endif
#define fail nd_internal_fail
static void put32(unsigned char *p, uint32_t x) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (unsigned char)(x >> (8 * i));
}
static void put64(unsigned char *p, uint64_t x) {
    for (unsigned i = 0; i < 8; ++i) p[i] = (unsigned char)(x >> (8 * i));
}
static void state_double(unsigned char *p, double value) {
    uint64_t bits; if (value == 0) value = 0;
    memcpy(&bits, &value, sizeof(bits)); put64(p, bits);
}
static size_t capacity_for(size_t old, size_t needed) {
    size_t capacity = old ? old : 16;
    while (capacity < needed) {
        if (capacity > (size_t)(NONE - 1) / 2) return needed;
        capacity *= 2;
    }
    return capacity;
}
static int state_id_compare(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}
typedef struct { unsigned char *p; size_t n, capacity; } bs_buffer;
struct bs_engine {
    nd_engine *e;
    uint64_t *ids, admitted, forgotten;
    size_t id_capacity;
    double last_arrival;
    int bounded;
    bs_buffer completions, state;
};
static int bytes(bs_buffer *b, const void *data, size_t n) {
    if (n > SIZE_MAX - b->n) return -1;
    size_t needed = b->n + n;
    if (needed > b->capacity) {
        size_t cap = b->capacity ? b->capacity : 256;
        while (cap < needed) {
            if (cap > SIZE_MAX / 2) { cap = needed; break; }
            cap *= 2;
        }
        unsigned char *p = realloc(b->p, cap);
        if (!p) return -1;
        b->p = p; b->capacity = cap;
    }
    memcpy(b->p + b->n, data, n); b->n += n;
    return 0;
}
static int u32(bs_buffer *b, uint32_t value) {
    unsigned char p[4]; put32(p, value); return bytes(b, p, sizeof(p));
}
static int u64(bs_buffer *b, uint64_t value) {
    unsigned char p[8]; put64(p, value); return bytes(b, p, sizeof(p));
}
static int real(bs_buffer *b, double value) {
    unsigned char p[8]; state_double(p, value); return bytes(b, p, sizeof(p));
}
#define WRITE(expression) do { if ((expression)) return -2; } while (0)
/* Stable logical reference, never an address or recyclable physical offset. */
static int operation(bs_engine *b, bs_buffer *out, uint32_t op) {
    if (op == NONE) return u64(out, UINT64_MAX) || u32(out, NONE);
    uint32_t job = b->e->job_of[op];
    return u64(out, b->ids[job]) || u32(out, op - b->e->offsets[job]);
}
static int completed(void *context, uint32_t job) {
    bs_engine *b = context;
    nd_engine *e = b->e; bs_buffer *out = &b->completions;
    WRITE(u64(out, b->ids[job]));
    WRITE(real(out, e->arrivals[job])); WRITE(real(out, e->due[job]));
    WRITE(real(out, e->released[job])); WRITE(real(out, e->finished[job]));
    WRITE(u32(out, e->offsets[job + 1] - e->offsets[job]));
    for (uint32_t op = e->offsets[job]; op < e->offsets[job + 1]; ++op) {
        WRITE(u32(out, e->routes[op])); WRITE(real(out, e->durations[op]));
        WRITE(real(out, e->queued[op])); WRITE(real(out, e->starts[op])); WRITE(real(out, e->ends[op]));
    }
    return 0;
}
/* Stable compaction: map all references before moving any source array. Queue,
 * pool and calendar order are unchanged; no event is processed here. */
static int compact(bs_engine *b) {
    nd_engine *e = b->e;
    if (!b->bounded) return 0;
    uint32_t removed = 0;
    for (uint32_t j = 0; j < e->jobs; ++j) removed += e->job_state[j] == 3;
    if (!removed) return 0;
    uint32_t *jm = calloc(e->jobs, sizeof(*jm));
    uint32_t *om = calloc(e->operations, sizeof(*om));
    if (!jm || !om) { free(jm); free(om); return fail(e, "compaction allocation failed"); }
    uint32_t jobs = 0, ops = 0;
    for (uint32_t j = 0; j < e->jobs; ++j) {
        jm[j] = e->job_state[j] == 3 ? NONE : jobs++;
        for (uint32_t op = e->offsets[j]; op < e->offsets[j + 1]; ++op)
            om[op] = jm[j] == NONE ? NONE : ops++;
    }
    for (size_t i = 0; i < e->heap_size; ++i) {
        event *ev = e->heap + i;
        if (ev->kind == ARRIVAL) ev->id = jm[ev->id];
        else if (ev->kind != RELEASE_DISPATCH && ev->kind != POLICY_WAKE) ev->id = om[ev->id];
        /* A finished job cannot own a remaining arrival/operation event. */
        if (ev->id == NONE) { free(jm); free(om); return fail(e, "dangling compacted event"); }
    }
    for (uint32_t s = 0; s < e->servers; ++s) {
        if (e->head[s] != NONE) e->head[s] = om[e->head[s]];
        if (e->tail[s] != NONE) e->tail[s] = om[e->tail[s]];
    }
    for (size_t i = 0; i < e->pool_size; ++i) e->pool[i] = jm[e->pool[i]];
    size_t dispatched = 0;
    for (size_t i = 0; i < e->dispatched; ++i)
        if (om[e->dispatch[i]] != NONE) e->dispatch[dispatched++] = om[e->dispatch[i]];
    for (uint32_t op = 0; op < e->operations; ++op) {
        uint32_t dst = om[op];
        if (dst == NONE) continue;
        e->durations[dst] = e->durations[op]; e->routes[dst] = e->routes[op];
        e->job_of[dst] = jm[e->job_of[op]];
        e->next[dst] = e->next[op] == NONE ? NONE : om[e->next[op]];
        e->queued[dst] = e->queued[op]; e->starts[dst] = e->starts[op]; e->ends[dst] = e->ends[op];
    }
    uint32_t begin = 0;
    for (uint32_t j = 0; j < e->jobs; ++j) {
        uint32_t count = e->offsets[j + 1] - e->offsets[j], dst = jm[j];
        if (dst == NONE) continue;
        e->arrivals[dst] = e->arrivals[j]; e->due[dst] = e->due[j];
        e->released[dst] = e->released[j]; e->finished[dst] = e->finished[j];
        e->job_state[dst] = e->job_state[j]; b->ids[dst] = b->ids[j];
        e->offsets[dst] = begin; begin += count;
    }
    e->offsets[jobs] = ops;
    e->jobs = jobs; e->operations = ops; e->dispatched = dispatched;
    b->forgotten += removed;
    free(jm); free(om);
    return 0;
}
bs_engine *bs_create(uint32_t servers, uint32_t cap, int policy, int bounded,
                     double warmup, char *error, size_t capacity) {
    bs_engine *b = calloc(1, sizeof(*b));
    if (!b) { if (error && capacity) snprintf(error, capacity, "stream allocation failed"); return NULL; }
    b->bounded = bounded;
    b->e = nd_create(servers, cap, policy, 0, NULL, error, capacity);
    if (!b->e) { free(b); return NULL; }
    if (nd_enable_stock_kpis(b->e, warmup)) {
        if (error && capacity) snprintf(error, capacity, "%s", nd_error(b->e));
        bs_destroy(b); return NULL;
    }
    return b;
}
int bs_add(bs_engine *b, double arrival, double due, uint32_t count,
           const uint32_t *routes, const double *durations, uint64_t *identity) {
    if (!b || !b->e || !identity) return -1;
    nd_engine *e = b->e;
    if (b->admitted == UINT64_MAX) return fail(e, "logical identity overflow");
    if (arrival < b->last_arrival) return fail(e, "arrival order changed");
    if ((size_t)e->jobs + 1 > b->id_capacity) {
        size_t cap = capacity_for(b->id_capacity, (size_t)e->jobs + 1);
        uint64_t *p = realloc(b->ids, cap * sizeof(*p));
        if (!p) return fail(e, "identity allocation failed");
        b->ids = p; b->id_capacity = cap;
    }
    uint32_t physical;
    if (nd_add(e, arrival, due, count, routes, count, durations, count, &physical)) return -1;
    *identity = b->ids[physical] = b->admitted++;
    b->last_arrival = arrival;
    return 0;
}
int bs_advance(bs_engine *b, double until) {
    if (!b || !b->e) return -1;
    b->completions.n = 0;
    if (nd_internal_advance(b->e, until, completed, b)) return -1;
    return compact(b);
}
int bs_run(bs_engine *b) {
    if (!b || !b->e) return -1;
    b->completions.n = 0;
    if (nd_internal_run(b->e, b->forgotten, completed, b)) return -1;
    return compact(b);
}
int bs_inspect(const bs_engine *b, uint64_t id, nd_snapshot *out) {
    if (!b || !b->e || b->e->state == 1 || id >= b->admitted) return -1;
    size_t low = 0, high = b->e->jobs;
    while (low < high) { size_t mid = low + (high - low) / 2; if (b->ids[mid] < id) low = mid + 1; else high = mid; }
    if (low == b->e->jobs || b->ids[low] != id) return -3;
    return nd_inspect(b->e, (uint32_t)low, out);
}
int bs_kpis(const bs_engine *b, nd_kpi_snapshot *out) { return b ? nd_stock_kpis(b->e, out) : -1; }
int bs_completions(const bs_engine *b, const unsigned char **p, size_t *n) {
    if (!b || !b->e || b->e->state == 1 || !p || !n) return -1;
    *p = b->completions.p; *n = b->completions.n; return 0;
}
int bs_set_capacities(bs_engine *b, const uint32_t *capacities, size_t count) {
    if (!b || !b->e) return -1;
    /* Compaction may leave no live jobs; lifetime admissions still forbid setup. */
    if (b->admitted) return fail(b->e, "capacity configuration after admission");
    return nd_set_capacities(b->e, capacities, count);
}
int bs_state(bs_engine *b, const unsigned char **p, size_t *n) {
    if (!b || !b->e || b->e->state == 1 || !p || !n) return -1;
    nd_engine *e = b->e; bs_buffer *out = &b->state; out->n = 0;
    WRITE(bytes(out, e->capacities ? "NBST0002" : "NBST0001", 8));
    WRITE(u32(out, e->servers)); WRITE(u32(out, e->cap)); WRITE(u32(out, (uint32_t)e->policy));
    WRITE(u32(out, (uint32_t)e->state)); WRITE(u32(out, (uint32_t)e->policy_pending));
    WRITE(real(out, e->now)); WRITE(real(out, b->last_arrival));
    WRITE(u64(out, b->admitted)); WRITE(u64(out, e->sequence)); WRITE(u64(out, e->floor_wip));
    WRITE(u64(out, e->result.completed)); WRITE(u64(out, e->result.events));
    WRITE(real(out, e->result.makespan)); WRITE(u32(out, (uint32_t)e->stock_horizon));
    uint32_t live = 0;
    for (uint32_t j = 0; j < e->jobs; ++j) live += e->job_state[j] != 3;
    WRITE(u32(out, live));
    for (uint32_t s = 0; s < e->servers; ++s) {
        if (e->capacities) WRITE(u32(out, e->capacities[s]));
        WRITE(u32(out, e->occupied[s])); WRITE(operation(b, out, e->head[s])); WRITE(operation(b, out, e->tail[s]));
    }
    for (uint32_t j = 0; j < e->jobs; ++j) {
        if (e->job_state[j] == 3) continue;
        WRITE(u64(out, b->ids[j])); WRITE(real(out, e->arrivals[j])); WRITE(real(out, e->due[j]));
        WRITE(real(out, e->released[j])); WRITE(real(out, e->finished[j])); WRITE(u32(out, e->job_state[j]));
        WRITE(u32(out, e->offsets[j + 1] - e->offsets[j]));
        for (uint32_t op = e->offsets[j]; op < e->offsets[j + 1]; ++op) {
            WRITE(u32(out, e->routes[op])); WRITE(real(out, e->durations[op]));
            WRITE(real(out, e->queued[op])); WRITE(real(out, e->starts[op])); WRITE(real(out, e->ends[op]));
            WRITE(operation(b, out, e->next[op]));
        }
    }
    uint32_t *pool = NULL; event *calendar = NULL;
    if (e->pool_size) {
        pool = calloc(e->pool_size, sizeof(*pool));
        if (!pool) return -2;
        memcpy(pool, e->pool, e->pool_size * sizeof(*pool));
        qsort(pool, e->pool_size, sizeof(*pool), state_id_compare);
    }
    if (e->heap_size) {
        calendar = calloc(e->heap_size, sizeof(*calendar));
        if (!calendar) { free(pool); return -2; }
        memcpy(calendar, e->heap, e->heap_size * sizeof(*calendar));
        qsort(calendar, e->heap_size, sizeof(*calendar), nd_internal_event_compare);
    }
    int rc = u64(out, e->pool_size);
    for (size_t i = 0; !rc && i < e->pool_size; ++i) rc = u64(out, b->ids[pool[i]]);
    if (!rc) rc = u64(out, e->heap_size);
    for (size_t i = 0; !rc && i < e->heap_size; ++i) {
        event ev = calendar[i];
        rc = real(out, ev.time) || u64(out, ev.sequence) || u32(out, ev.kind);
        if (rc) break;
        if (ev.kind == ARRIVAL) rc = u64(out, b->ids[ev.id]) || u32(out, NONE);
        else if (ev.kind == RELEASE_DISPATCH || ev.kind == POLICY_WAKE) rc = u64(out, ev.id) || u32(out, NONE);
        else rc = operation(b, out, ev.id);
    }
    free(pool); free(calendar);
    if (rc) return -2;
    *p = out->p; *n = out->n; return 0;
}
#undef WRITE
int bs_statistics(const bs_engine *b, bs_stats *out) {
    if (!b || !b->e || b->e->state == 1 || !out) return -1;
    nd_engine *e = b->e;
    if (nd_storage(e, out)) return -1;
    out->admitted = b->admitted; out->forgotten = b->forgotten;
    out->completion_capacity = b->completions.capacity; out->state_capacity = b->state.capacity;
    out->native_bytes += sizeof(*b) + b->id_capacity * sizeof(uint64_t) +
        b->completions.capacity + b->state.capacity;
    return 0;
}
double bs_now(const bs_engine *b) { return b ? nd_now(b->e) : NAN; }
const char *bs_error(const bs_engine *b) { return b ? nd_error(b->e) : "null stream"; }
void bs_destroy(bs_engine *b) {
    if (!b) return;
    nd_destroy(b->e); free(b->ids); free(b->completions.p); free(b->state.p); free(b);
}
#ifdef ND_TEST_ALLOCATIONS
size_t bs_test_attempts(void) { return nd_internal_test_attempts(); }
void bs_test_identity(bs_engine *b, uint64_t value) { b->admitted = value; }
#endif
