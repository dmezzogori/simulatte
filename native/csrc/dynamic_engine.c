#include "dynamic_engine.h"
#include "dynamic_engine_internal.h"

#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef ND_TEST_ALLOCATIONS
static size_t allocation_attempt, allocation_failure;
void nd_test_fail_allocation(size_t nth) { allocation_attempt = 0; allocation_failure = nth; }
void *nd_test_calloc(size_t count, size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return calloc(count, size);
}
void *nd_test_realloc(void *p, size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return realloc(p, size);
}
#define calloc nd_test_calloc
#define realloc nd_test_realloc
#endif

_Static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53 && DBL_MAX_EXP == 1024,
               "native engine requires IEEE binary64 doubles");
#ifdef ND_TEST_ALLOCATIONS
void nd_test_sequence(nd_engine *e, uint64_t value) { e->sequence = value; }
#endif

static int fail(nd_engine *e, const char *message) {
    e->state = 1;
    (void)snprintf(e->error, sizeof(e->error), "%s", message);
    return -1;
}

static void *allocate(nd_engine *e, size_t count, size_t size) {
    if (count > SIZE_MAX / size) {
        (void)fail(e, "allocation size overflow");
        return NULL;
    }
    void *p = calloc(count ? count : 1, size);
    if (!p) (void)fail(e, "allocation failed");
    return p;
}

static int before(event a, event b) {
    if (a.time != b.time) return a.time < b.time;
    /* SimPy process initializations use URGENT priority. Preserve sequence
     * order between arrival processes and newly released job processes. */
    int urgent_a = a.kind == ARRIVAL || a.kind == INITIALIZE;
    int urgent_b = b.kind == ARRIVAL || b.kind == INITIALIZE;
    if (urgent_a != urgent_b) return urgent_a;
    return a.sequence < b.sequence;
}

static int schedule(nd_engine *e, double time, uint32_t kind, uint32_t id) {
    if (!isfinite(time) || time < e->now) return fail(e, "invalid event time");
    if (e->sequence == UINT64_MAX) return fail(e, "event sequence overflow");
    if (e->heap_size == e->heap_capacity) {
        size_t capacity = e->heap_capacity ? e->heap_capacity * 2 : 32;
        if (capacity < e->heap_capacity || capacity > SIZE_MAX / sizeof(event))
            return fail(e, "calendar allocation size overflow");
        event *heap = realloc(e->heap, capacity * sizeof(event));
        if (!heap) return fail(e, "calendar allocation failed");
        e->heap = heap;
        e->heap_capacity = capacity;
    }
    event value = {time, e->sequence++, kind, id};
    size_t i = e->heap_size++;
    while (i && before(value, e->heap[(i - 1) / 2])) {
        e->heap[i] = e->heap[(i - 1) / 2];
        i = (i - 1) / 2;
    }
    e->heap[i] = value;
    return 0;
}

static event pop(nd_engine *e) {
    event first = e->heap[0];
    event last = e->heap[--e->heap_size];
    size_t i = 0;
    while (2 * i + 1 < e->heap_size) {
        size_t child = 2 * i + 1;
        if (child + 1 < e->heap_size && before(e->heap[child + 1], e->heap[child]))
            ++child;
        if (!before(e->heap[child], last)) break;
        e->heap[i] = e->heap[child];
        i = child;
    }
    if (e->heap_size) e->heap[i] = last;
    return first;
}

static void put32(unsigned char *p, uint32_t x) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (unsigned char)(x >> (8 * i));
}
static void put64(unsigned char *p, uint64_t x) {
    for (unsigned i = 0; i < 8; ++i) p[i] = (unsigned char)(x >> (8 * i));
}

static int flush_records(nd_engine *e) {
    if (e->record_used && fwrite(e->record_buffer, 1, e->record_used, e->file) != e->record_used)
        return fail(e, "record write failed");
    e->record_used = 0;
    return 0;
}

static int record(nd_engine *e, uint32_t kind, uint32_t job, uint32_t op, uint32_t server) {
    if (e->mode < 2) return 0;
    if (e->result.records == UINT64_MAX) return fail(e, "record sequence overflow");
    if (e->record_used + 32 > RECORD_BUFFER_SIZE && flush_records(e)) return -1;
    unsigned char *p = e->record_buffer + e->record_used;
    uint64_t bits;
    memcpy(&bits, &e->now, sizeof(bits));
    put64(p, bits);
    put64(p + 8, e->result.records++);
    put32(p + 16, kind);
    put32(p + 20, job);
    put32(p + 24, op);
    put32(p + 28, server);
    e->record_used += 32;
    return 0;
}

static int wip_change(nd_engine *e, int delta) {
    if (!e->mode) return 0;
    e->result.wip_area += (e->now - e->last_wip_time) * (double)e->wip;
    e->last_wip_time = e->now;
    if (delta > 0) ++e->wip;
    else --e->wip;
    if (e->wip > e->result.max_wip) e->result.max_wip = e->wip;
    return 0;
}

static int grant(nd_engine *e, uint32_t server) {
    if (e->occupied[server] || e->head[server] == NONE) return 0;
    uint32_t chosen = e->head[server];
    if (e->policy == 1) {
        for (uint32_t op = e->next[chosen]; op != NONE; op = e->next[op])
            if (e->durations[op] < e->durations[chosen]) chosen = op;
    }
    uint32_t previous = NONE, current = e->head[server];
    while (current != NONE && current != chosen) {
        previous = current;
        current = e->next[current];
    }
    if (current == NONE) return fail(e, "dispatch callback selected a noncandidate");
    if (previous == NONE) e->head[server] = e->next[chosen];
    else e->next[previous] = e->next[chosen];
    if (e->tail[server] == chosen) e->tail[server] = previous;
    e->next[chosen] = NONE;
    if (e->stock && nd_stock_busy(e->stock, e->now, 1)) return fail(e, "KPI allocation failed");
    e->occupied[server] = 1;
    if (e->mode == 3 && record(e, 8, e->job_of[chosen], chosen, server)) return -1;
    return schedule(e, e->now, START, chosen);
}

static int enqueue(nd_engine *e, uint32_t op) {
    uint32_t server = e->routes[op];
    e->queued[op] = e->now;
    e->next[op] = NONE;
    if (e->tail[server] == NONE) e->head[server] = op;
    else e->next[e->tail[server]] = op;
    e->tail[server] = op;
    if (record(e, 2, e->job_of[op], op, server)) return -1;
    return grant(e, server);
}

/* The EDD pool uses IDs as its stable registration-order tie breaker. */
static int pool_before(const nd_engine *e, uint32_t a, uint32_t b) {
    return e->due[a] < e->due[b] || (e->due[a] == e->due[b] && a < b);
}
static void pool_push(nd_engine *e, uint32_t job) {
    size_t i = e->pool_size++;
    while (i && pool_before(e, job, e->pool[(i - 1) / 2])) {
        e->pool[i] = e->pool[(i - 1) / 2];
        i = (i - 1) / 2;
    }
    e->pool[i] = job;
}
static uint32_t pool_pop(nd_engine *e) {
    uint32_t first = e->pool[0], last = e->pool[--e->pool_size];
    size_t i = 0;
    while (2 * i + 1 < e->pool_size) {
        size_t child = 2 * i + 1;
        if (child + 1 < e->pool_size && pool_before(e, e->pool[child + 1], e->pool[child])) ++child;
        if (!pool_before(e, e->pool[child], last)) break;
        e->pool[i] = e->pool[child];
        i = child;
    }
    if (e->pool_size) e->pool[i] = last;
    return first;
}
static int floor_change(nd_engine *e, int delta) {
    if (e->stock && nd_stock_floor(e->stock, e->now, delta)) return fail(e, "KPI allocation failed");
    if (e->mode) {
        e->result.floor_wip_area += (e->now - e->last_floor_time) * (double)e->floor_wip;
        e->last_floor_time = e->now;
    }
    if (delta > 0) ++e->floor_wip;
    else --e->floor_wip;
    if (e->floor_wip > e->result.max_floor_wip) e->result.max_floor_wip = e->floor_wip;
    return 0;
}
static int release_pending(nd_engine *e) {
    while (e->pool_size && (!e->cap || e->floor_wip < e->cap)) {
        uint32_t job = pool_pop(e);
        e->job_state[job] = 2;
        e->released[job] = e->now;
        if (floor_change(e, 1)) return -1;
        if (e->mode) e->result.total_pool_wait += e->now - e->arrivals[job];
        if (record(e, 6, job, NONE, NONE) || schedule(e, e->now, INITIALIZE, e->offsets[job])) return -1;
    }
    return 0;
}
static int process(nd_engine *e, event ev) {
    uint32_t op = ev.id;
    if (ev.kind == ARRIVAL) {
        e->job_state[op] = 1;
        if (record(e, 1, op, NONE, NONE) || wip_change(e, 1)) return -1;
        pool_push(e, op);
        return release_pending(e);
    }
    if (ev.kind == RELEASE_DISPATCH) return grant(e, op);
    if (ev.kind == INITIALIZE) return enqueue(e, op);
    if (ev.kind == POLICY_WAKE) {
        if (release_pending(e)) return -1;
        e->policy_pending = 0;
        return 0;
    }
    uint32_t job = e->job_of[op], server = e->routes[op];
    if (ev.kind == START) {
        e->starts[op] = e->now;
        e->dispatch[e->dispatched++] = op;
        if (record(e, 3, job, op, server)) return -1;
        if (e->mode) e->result.total_wait += e->now - e->queued[op];
        double end = e->now + e->durations[op];
        if (!isfinite(end) || end <= e->now) return fail(e, "operation end time overflow or lost precision");
        return schedule(e, end, END, op);
    }
    if (ev.kind == END) {
        e->ends[op] = e->now;
        if (e->mode) e->busy[server] += e->durations[op];
        if (record(e, 4, job, op, server)) return -1;
        return schedule(e, e->now, ADVANCE, op);
    }
    if (e->stock && nd_stock_busy(e->stock, e->now, -1)) return fail(e, "KPI allocation failed");
    e->occupied[server] = 0;
    if (e->mode == 3 && record(e, 9, job, op, server)) return -1;
    /* main() signals job_processing_end before constructing the Release.
     * The waiting policy process resumes later at NORMAL priority. Multiple
     * signals before that resume wake it once, then it waits on the latest
     * signal event. This must also run for non-final operations. */
    if (e->cap && !e->policy_pending) {
        if (schedule(e, e->now, POLICY_WAKE, 0)) return -1;
        e->policy_pending = 1;
    }
    if (schedule(e, e->now, RELEASE_DISPATCH, server)) return -1;
    if (op + 1 < e->offsets[job + 1]) return enqueue(e, op + 1);
    e->finished[job] = e->now;
    e->result.makespan = e->now; /* Completion time, independent of idle advance. */
    e->job_state[job] = 3;
    ++e->result.completed;
    if (e->mode) {
        e->result.total_flow += e->now - e->arrivals[job];
        e->result.total_tardiness += e->now > e->due[job] ? e->now - e->due[job] : 0;
    }
    if (record(e, 5, job, NONE, NONE) || wip_change(e, -1)) return -1;
    if (e->stock) {
        uint32_t begin = e->offsets[job];
        int rc = nd_stock_finish(e->stock, e->now, e->arrivals[job], e->due[job],
            e->offsets[job + 1] - begin, e->ends + begin, e->queued + begin, e->durations + begin);
        if (rc) return fail(e, rc == -2 ? "job queue sum overflow" : "KPI allocation failed");
    }
    return floor_change(e, -1);
}

/* Each successful realloc is installed immediately. A later failure poisons
 * the engine, leaving every allocation owned and destroyable; no partially
 * grown arrays can be used again. Counts/capacities commit only on success. */
static int grow_field(nd_engine *e, void **field, size_t count, size_t size) {
    if (count > SIZE_MAX / size) return fail(e, "allocation size overflow");
    void *replacement = realloc(*field, count * size);
    if (!replacement) return fail(e, "growth allocation failed");
    *field = replacement;
    return 0;
}
static size_t capacity_for(size_t old, size_t needed) {
    size_t capacity = old ? old : 16;
    while (capacity < needed) {
        if (capacity > (size_t)(NONE - 1) / 2) return needed;
        capacity *= 2;
    }
    return capacity;
}
static int grow_jobs(nd_engine *e, size_t needed) {
    if (needed <= e->job_capacity) return 0;
    size_t capacity = capacity_for(e->job_capacity, needed);
#define GROW(field, count) do { \
    void *p = e->field; \
    if (grow_field(e, &p, (count), sizeof(*e->field))) return -1; \
    e->field = p; \
} while (0)
    GROW(arrivals, capacity); GROW(due, capacity); GROW(finished, capacity);
    GROW(released, capacity); GROW(offsets, capacity + 1);
    GROW(job_state, capacity); GROW(pool, capacity);
    e->job_capacity = capacity;
    return 0;
}
static int grow_operations(nd_engine *e, size_t needed) {
    if (needed <= e->op_capacity) return 0;
    size_t capacity = capacity_for(e->op_capacity, needed);
    GROW(durations, capacity); GROW(routes, capacity); GROW(job_of, capacity);
    GROW(next, capacity); GROW(queued, capacity); GROW(starts, capacity);
    GROW(ends, capacity); GROW(dispatch, capacity);
#undef GROW
    e->op_capacity = capacity;
    return 0;
}
int nd_add(nd_engine *e, double arrival, double due, uint32_t count,
           const uint32_t *routes, size_t route_length,
           const double *durations, size_t duration_length, uint32_t *id) {
    if (!e) return -1;
    if (e->state) return fail(e, "engine is sealed or failed");
    if (!count || count == NONE || e->jobs >= NONE - 1 || count >= NONE - e->operations ||
        route_length != count || duration_length != count || !routes || !durations || !id)
        return fail(e, "invalid input counts or array lengths");
    if (!isfinite(arrival) || arrival < e->now || !isfinite(due) ||
        (e->jobs && arrival < e->arrivals[e->jobs - 1]))
        return fail(e, "invalid arrival or due date");
    for (uint32_t op = 0; op < count; ++op) {
        if (routes[op] >= e->servers || !isfinite(durations[op]) || durations[op] <= 0)
            return fail(e, "invalid operation server or duration");
        for (uint32_t prior = 0; prior < op; ++prior)
            if (routes[prior] == routes[op]) return fail(e, "repeated server in route");
    }
    if (grow_jobs(e, (size_t)e->jobs + 1) || grow_operations(e, (size_t)e->operations + count)) return -1;
    uint32_t job = e->jobs, begin = e->operations;
    e->arrivals[job] = arrival;
    e->due[job] = due;
    e->finished[job] = e->released[job] = 0;
    e->job_state[job] = 0;
    e->offsets[job] = begin;
    e->offsets[job + 1] = begin + count;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t op = begin + i;
        e->durations[op] = durations[i];
        e->routes[op] = routes[i];
        e->job_of[op] = job;
        e->next[op] = NONE;
        e->queued[op] = e->starts[op] = e->ends[op] = 0;
    }
    if (schedule(e, arrival, ARRIVAL, job)) return -1;
    ++e->jobs;
    e->operations += count;
    if (e->mode == 3 && record(e, 7, job, NONE, NONE)) return -1;
    *id = job;
    return 0;
}

int nd_add_many(nd_engine *e, uint32_t jobs,
                const double *arrivals, size_t arrival_length,
                const double *due, size_t due_length,
                const uint32_t *offsets, size_t offset_length,
                const uint32_t *routes, size_t route_length,
                const double *durations, size_t duration_length, uint32_t *first_id) {
    if (!e) return -1;
    if (e->state) return fail(e, "engine is sealed or failed");
    if (!jobs || jobs >= NONE - e->jobs || arrival_length != jobs || due_length != jobs ||
        offset_length != (size_t)jobs + 1 || !route_length || route_length >= NONE - e->operations ||
        duration_length != route_length || !arrivals || !due || !offsets || !routes || !durations || !first_id)
        return fail(e, "invalid batch counts or array lengths");
    if (offsets[0] || offsets[jobs] != route_length)
        return fail(e, "invalid batch offsets");
    for (uint32_t j = 0; j < jobs; ++j)
        if (offsets[j] >= offsets[j + 1] || offsets[j + 1] > route_length)
            return fail(e, "invalid batch offsets");
    uint32_t first = e->jobs, identity;
    for (uint32_t j = 0; j < jobs; ++j) {
        uint32_t begin = offsets[j], count = offsets[j + 1] - begin;
        if (nd_add(e, arrivals[j], due[j], count, routes + begin, count,
                   durations + begin, count, &identity)) return -1;
    }
    *first_id = first;
    return 0;
}

nd_engine *nd_create(uint32_t servers, uint32_t cap, int policy, int mode,
                     const char *path, char *error, size_t error_capacity) {
    nd_engine *e = calloc(1, sizeof(*e));
    if (!e) {
        if (error && error_capacity) (void)snprintf(error, error_capacity, "engine allocation failed");
        return NULL;
    }
    if (!servers || servers == NONE || cap == NONE || (policy != 0 && policy != 1) ||
        mode < 0 || mode > 3 || ((mode >= 2) != (path != NULL)) || (path && !*path)) {
        (void)fail(e, "invalid server count, cap, policy, mode or record path");
        goto failure;
    }
    e->servers = servers;
    e->cap = cap;
    e->policy = policy;
    e->mode = mode;
#define ALLOC(field, count) do { \
    e->field = allocate(e, (count), sizeof(*e->field)); \
    if (!e->field) goto failure; \
} while (0)
    ALLOC(head, servers); ALLOC(tail, servers); ALLOC(occupied, servers); ALLOC(busy, servers);
    if (mode >= 2) { ALLOC(record_buffer, RECORD_BUFFER_SIZE); }
#undef ALLOC
    for (uint32_t s = 0; s < servers; ++s) e->head[s] = e->tail[s] = NONE;
    if (mode >= 2) {
        e->file = fopen(path, "wb");
        if (!e->file) { (void)fail(e, "cannot open record file"); goto failure; }
        (void)setvbuf(e->file, NULL, _IONBF, 0);
        memcpy(e->record_buffer, mode == 3 ? "NTRC0001" : "NDYN0001", 8);
        e->record_used = 8;
    }
    return e;
failure:
    if (error && error_capacity) (void)snprintf(error, error_capacity, "%s", e->error);
    nd_destroy(e);
    return NULL;
}
static int drain(nd_engine *e, double until, nd_completion_callback callback, void *context) {
    while (e->heap_size && e->heap[0].time < until) {
        event ev = pop(e);
        e->now = ev.time;
        if (e->result.events == UINT64_MAX) return fail(e, "event counter overflow");
        ++e->result.events;
        if (process(e, ev)) return -1;
        if (callback && ev.kind == ADVANCE && e->job_state[e->job_of[ev.id]] == 3 &&
            callback(context, e->job_of[ev.id])) return fail(e, "completion allocation failed");
    }
    return 0;
}
int nd_internal_advance(nd_engine *e, double until, nd_completion_callback callback, void *context) {
    if (!e) return -1;
    if (e->state || !isfinite(until) || until < e->now) return fail(e, "invalid advance or engine state");
    double previous = e->now;
    if (drain(e, until, callback, context)) return -1;
    if (until > previous) e->stock_horizon = 1;
    e->now = until;
    return 0;
}
int nd_flush(nd_engine *e) {
    if (!e) return -1;
    if (e->state == 1) return fail(e, "engine is running or failed");
    if (e->file && (flush_records(e) || fflush(e->file))) return fail(e, "record flush failed");
    return 0;
}
int nd_internal_run(nd_engine *e, uint64_t forgotten, nd_completion_callback callback, void *context) {
    if (!e) return -1;
    if (e->state) return fail(e, "engine can only run once");
    e->state = 1;
    if (forgotten > e->result.completed) return fail(e, "invalid forgotten accounting");
    if (drain(e, INFINITY, callback, context)) return -1;
    if (e->result.completed - forgotten != e->jobs || e->dispatched != e->operations || e->floor_wip || e->pool_size)
        return fail(e, "incomplete simulation");
    if (e->mode >= 2 && (flush_records(e) || fflush(e->file))) return fail(e, "record flush failed");
    e->result.starts = e->starts;
    e->result.ends = e->ends;
    e->result.finished = e->finished;
    e->result.released = e->released;
    e->result.dispatch = e->dispatch;
    e->result.busy = e->busy;
    e->state = 2;
    e->stock_horizon = 0;
    return 0;
}
int nd_enable_stock_kpis(nd_engine *e, double warmup) {
    if (!e) return -1;
    if (e->state || e->stock || e->jobs || e->now != 0 || e->stock_horizon ||
        !isfinite(warmup) || warmup < 0) return fail(e, "invalid stock KPI configuration");
    e->stock = nd_stock_create(warmup);
    return e->stock ? 0 : fail(e, "KPI allocation failed");
}
int nd_stock_kpis(const nd_engine *e, nd_kpi_snapshot *out) {
    if (!e || e->state == 1 || !e->stock || !out) return -1;
    return nd_stock_snapshot(e->stock, e->now, e->servers, e->stock_horizon, out) ? -2 : 0;
}
int nd_inspect(const nd_engine *e, uint32_t id, nd_snapshot *out) {
    if (!e || e->state == 1 || id >= e->jobs || !out) return -1;
    out->arrival = e->arrivals[id];
    out->released = e->released[id];
    out->finished = e->finished[id];
    out->state = e->job_state[id];
    return 0;
}
static int state_size_add(size_t *size, size_t count, size_t width) {
    if (count > (SIZE_MAX - *size) / width) return -1;
    *size += count * width;
    return 0;
}
int nd_state_size(const nd_engine *e, size_t *out) {
    if (!e || e->state == 1 || !out) return -1;
    size_t size = 108; /* magic, seven u32s, now, seven u64s, makespan */
    if (state_size_add(&size, e->servers, 12) || state_size_add(&size, e->jobs, 36) ||
        state_size_add(&size, (size_t)e->jobs + 1, 4) || state_size_add(&size, e->operations, 44) ||
        state_size_add(&size, e->dispatched, 4) || state_size_add(&size, e->pool_size, 4) ||
        state_size_add(&size, e->heap_size, 24)) return -1;
    *out = size;
    return 0;
}
static int state_event_compare(const void *left, const void *right) {
    event a = *(const event *)left, b = *(const event *)right;
    if (before(a, b)) return -1;
    if (before(b, a)) return 1;
    if (a.kind != b.kind) return a.kind < b.kind ? -1 : 1;
    return a.id < b.id ? -1 : a.id > b.id;
}
static int state_id_compare(const void *left, const void *right) {
    uint32_t a = *(const uint32_t *)left, b = *(const uint32_t *)right;
    return a < b ? -1 : a > b;
}
static void state_double(unsigned char *p, double value) {
    uint64_t bits;
    if (value == 0) value = 0; /* canonicalize signed zero */
    memcpy(&bits, &value, sizeof(bits));
    put64(p, bits);
}
int nd_state_write(const nd_engine *e, unsigned char *destination, size_t capacity) {
    size_t size;
    if (nd_state_size(e, &size) || capacity != size || !destination) return -1;
    event *calendar = NULL;
    uint32_t *pool = NULL;
    /* Canonicalize logical membership/order without changing live heaps. */
    if (e->heap_size) {
        if (e->heap_size > SIZE_MAX / sizeof(event)) return -2;
        calendar = calloc(e->heap_size, sizeof(event));
        if (!calendar) return -2;
        memcpy(calendar, e->heap, e->heap_size * sizeof(event));
        qsort(calendar, e->heap_size, sizeof(event), state_event_compare);
    }
    if (e->pool_size) {
        pool = calloc(e->pool_size, sizeof(uint32_t));
        if (!pool) { free(calendar); return -2; }
        memcpy(pool, e->pool, e->pool_size * sizeof(uint32_t));
        qsort(pool, e->pool_size, sizeof(uint32_t), state_id_compare);
    }
    unsigned char *p = destination;
    memcpy(p, "NSTA0001", 8); p += 8;
#define S32(value) do { put32(p, (uint32_t)(value)); p += 4; } while (0)
#define S64(value) do { put64(p, (uint64_t)(value)); p += 8; } while (0)
#define SD(value) do { state_double(p, (value)); p += 8; } while (0)
    S32(e->servers); S32(e->cap); S32(e->policy); S32(e->state);
    S32(e->jobs); S32(e->operations); S32(e->policy_pending);
    SD(e->now);
    S64(e->sequence); S64(e->floor_wip); S64(e->dispatched);
    S64(e->result.completed); S64(e->result.events); S64(e->heap_size); S64(e->pool_size);
    SD(e->result.makespan);
    for (uint32_t i = 0; i < e->servers; ++i) {
        S32(e->head[i]); S32(e->tail[i]); S32(e->occupied[i]);
    }
    for (uint32_t i = 0; i < e->jobs; ++i) {
        SD(e->arrivals[i]); SD(e->due[i]); SD(e->released[i]); SD(e->finished[i]); S32(e->job_state[i]);
    }
    for (size_t i = 0; i <= e->jobs; ++i) S32(e->jobs ? e->offsets[i] : 0);
    for (uint32_t i = 0; i < e->operations; ++i) {
        S32(e->job_of[i]); S32(e->routes[i]); S32(e->next[i]);
        SD(e->durations[i]); SD(e->queued[i]); SD(e->starts[i]); SD(e->ends[i]);
    }
    for (size_t i = 0; i < e->dispatched; ++i) S32(e->dispatch[i]);
    for (size_t i = 0; i < e->pool_size; ++i) S32(pool[i]);
    for (size_t i = 0; i < e->heap_size; ++i) {
        SD(calendar[i].time); S64(calendar[i].sequence); S32(calendar[i].kind); S32(calendar[i].id);
    }
#undef S32
#undef S64
#undef SD
    free(calendar); free(pool);
    return 0;
}
double nd_now(const nd_engine *e) { return e ? e->now : NAN; }
const nd_result *nd_get_result(const nd_engine *e) { return e && e->state == 2 ? &e->result : NULL; }
const char *nd_error(const nd_engine *e) { return e ? e->error : "null engine"; }
void nd_destroy(nd_engine *e) {
    if (!e) return;
    if (e->file) { (void)flush_records(e); (void)fclose(e->file); }
    free(e->arrivals); free(e->due); free(e->durations); free(e->queued); free(e->released);
    free(e->offsets); free(e->routes); free(e->job_of); free(e->next); free(e->job_state); free(e->pool);
    free(e->head); free(e->tail); free(e->occupied);
    free(e->starts); free(e->ends); free(e->finished); free(e->busy); free(e->dispatch);
    free(e->heap); free(e->record_buffer);
    nd_stock_destroy(e->stock);
    free(e);
}

int nd_advance(nd_engine *e, double until) { return nd_internal_advance(e, until, NULL, NULL); }
int nd_run(nd_engine *e) { return nd_internal_run(e, 0, NULL, NULL); }
int nd_internal_fail(nd_engine *e, const char *text) { return fail(e, text); }
int nd_internal_event_compare(const void *a, const void *b) { return state_event_compare(a, b); }
int nd_storage(const nd_engine *e, nd_storage_stats *out) {
    if (!e || e->state == 1 || !out) return -1;
    *out = (nd_storage_stats) {0};
    out->admitted = out->jobs = e->jobs;
    out->completed = e->result.completed; out->events = e->result.events;
    out->operations = e->operations; out->job_capacity = e->job_capacity;
    out->op_capacity = e->op_capacity; out->calendar_capacity = e->heap_capacity;
    out->native_bytes = sizeof(*e) + e->job_capacity * 41 + (e->job_capacity ? 4 : 0) +
        e->op_capacity * 48 + e->heap_capacity * sizeof(event) + e->servers * 17 +
        (e->mode >= 2 ? RECORD_BUFFER_SIZE : 0) + nd_stock_bytes(e->stock);
    return 0;
}
#ifdef ND_TEST_ALLOCATIONS
size_t nd_internal_test_attempts(void) { return allocation_attempt; }
#endif
