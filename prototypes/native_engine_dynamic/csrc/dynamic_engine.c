#include "dynamic_engine.h"

#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef ND_TEST_ALLOCATIONS
static size_t allocation_attempt, allocation_failure;
void nd_test_fail_allocation(size_t nth) { allocation_attempt = 0; allocation_failure = nth; }
static void *test_calloc(size_t count, size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return calloc(count, size);
}
static void *test_realloc(void *p, size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return realloc(p, size);
}
#define calloc test_calloc
#define realloc test_realloc
#endif

_Static_assert(sizeof(double) == 8 && DBL_MANT_DIG == 53 && DBL_MAX_EXP == 1024,
               "native engine requires IEEE binary64 doubles");
#define NONE UINT32_MAX
#define RECORD_BUFFER_SIZE 65536u
enum { ARRIVAL, START, END, ADVANCE, RELEASE_DISPATCH, INITIALIZE, POLICY_WAKE };
typedef struct { double time; uint64_t sequence; uint32_t kind, id; } event;

struct nd_engine {
    uint32_t servers, jobs, operations, cap;
    int policy, mode, state; /* 0 accepting, 1 sealed/failed, 2 successful */
    int policy_pending;
    double now;
    uint64_t sequence, wip, floor_wip;
    double last_wip_time, last_floor_time;
    double *arrivals, *due, *durations, *queued, *released;
    uint32_t *offsets, *routes, *job_of, *next, *head, *tail, *pool;
    unsigned char *occupied, *job_state;
    double *starts, *ends, *finished, *busy;
    uint32_t *dispatch;
    size_t dispatched, job_capacity, op_capacity, pool_size;
    event *heap;
    size_t heap_size, heap_capacity;
    FILE *file;
    unsigned char *record_buffer;
    size_t record_used;
    nd_result result;
    char error[256];
};
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
static void floor_change(nd_engine *e, int delta) {
    if (e->mode) {
        e->result.floor_wip_area += (e->now - e->last_floor_time) * (double)e->floor_wip;
        e->last_floor_time = e->now;
    }
    if (delta > 0) ++e->floor_wip;
    else --e->floor_wip;
    if (e->floor_wip > e->result.max_floor_wip) e->result.max_floor_wip = e->floor_wip;
}
static int release_pending(nd_engine *e) {
    while (e->pool_size && (!e->cap || e->floor_wip < e->cap)) {
        uint32_t job = pool_pop(e);
        e->job_state[job] = 2;
        e->released[job] = e->now;
        floor_change(e, 1);
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
    floor_change(e, -1);
    return 0;
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
static int drain(nd_engine *e, double until) {
    while (e->heap_size && e->heap[0].time < until) {
        event ev = pop(e);
        e->now = ev.time;
        if (e->result.events == UINT64_MAX) return fail(e, "event counter overflow");
        ++e->result.events;
        if (process(e, ev)) return -1;
    }
    return 0;
}
int nd_advance(nd_engine *e, double until) {
    if (!e) return -1;
    if (e->state || !isfinite(until) || until < e->now) return fail(e, "invalid advance or engine state");
    if (drain(e, until)) return -1;
    e->now = until;
    return 0;
}
int nd_run(nd_engine *e) {
    if (!e) return -1;
    if (e->state) return fail(e, "engine can only run once");
    e->state = 1;
    if (drain(e, INFINITY)) return -1;
    if (e->result.completed != e->jobs || e->dispatched != e->operations || e->floor_wip || e->pool_size)
        return fail(e, "incomplete simulation");
    if (e->mode >= 2 && (flush_records(e) || fflush(e->file))) return fail(e, "record flush failed");
    e->result.starts = e->starts;
    e->result.ends = e->ends;
    e->result.finished = e->finished;
    e->result.released = e->released;
    e->result.dispatch = e->dispatch;
    e->result.busy = e->busy;
    e->state = 2;
    return 0;
}
int nd_inspect(const nd_engine *e, uint32_t id, nd_snapshot *out) {
    if (!e || e->state == 1 || id >= e->jobs || !out) return -1;
    out->arrival = e->arrivals[id];
    out->released = e->released[id];
    out->finished = e->finished[id];
    out->state = e->job_state[id];
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
    free(e);
}
