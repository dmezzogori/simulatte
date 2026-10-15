#include "native_engine.h"

#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef NE_TEST_ALLOCATIONS
static size_t allocation_attempt, allocation_failure;
void ne_test_fail_allocation(size_t nth) { allocation_attempt = 0; allocation_failure = nth; }
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
enum { ARRIVAL_BATCH, START, END, ADVANCE, RELEASE_DISPATCH };
typedef struct { double time; uint64_t sequence; uint32_t kind, id; } event;

struct ne_engine {
    uint32_t servers, jobs, operations;
    int policy, mode, state; /* 0 new, 1 running/failed, 2 successful */
    double now;
    uint64_t sequence, wip;
    double last_wip_time;
    double *arrivals, *due, *durations, *queued;
    uint32_t *offsets, *routes, *job_of, *next, *head, *tail;
    unsigned char *occupied;
    double *starts, *ends, *finished, *busy;
    uint32_t *dispatch;
    size_t dispatched;
    event *heap;
    size_t heap_size, heap_capacity;
    ne_hook hook;
    void *hook_context;
    uint32_t *candidate_ids;
    double *candidate_durations;
    FILE *file;
    unsigned char *record_buffer;
    size_t record_used;
    ne_result result;
    char error[256];
};

static int fail(ne_engine *e, const char *message) {
    (void)snprintf(e->error, sizeof(e->error), "%s", message);
    return -1;
}

static void *allocate(ne_engine *e, size_t count, size_t size) {
    if (count > SIZE_MAX / size) {
        (void)fail(e, "allocation size overflow");
        return NULL;
    }
    void *p = calloc(count ? count : 1, size);
    if (!p) (void)fail(e, "allocation failed");
    return p;
}

static int before(event a, event b) {
    return a.time < b.time || (a.time == b.time && a.sequence < b.sequence);
}

static int schedule(ne_engine *e, double time, uint32_t kind, uint32_t id) {
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

static event pop(ne_engine *e) {
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

static int flush_records(ne_engine *e) {
    if (e->record_used && fwrite(e->record_buffer, 1, e->record_used, e->file) != e->record_used)
        return fail(e, "record write failed");
    e->record_used = 0;
    return 0;
}

static int record(ne_engine *e, uint32_t kind, uint32_t job, uint32_t op, uint32_t server) {
    if (e->mode != 2) return 0;
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

static int wip_change(ne_engine *e, int delta) {
    if (!e->mode) return 0;
    e->result.wip_area += (e->now - e->last_wip_time) * (double)e->wip;
    e->last_wip_time = e->now;
    if (delta > 0) ++e->wip;
    else --e->wip;
    if (e->wip > e->result.max_wip) e->result.max_wip = e->wip;
    return 0;
}

static int grant(ne_engine *e, uint32_t server) {
    if (e->occupied[server] || e->head[server] == NONE) return 0;
    uint32_t chosen = e->head[server];
    if (e->hook) {
        size_t count = 0;
        for (uint32_t op = e->head[server]; op != NONE; op = e->next[op]) {
            e->candidate_ids[count] = op;
            e->candidate_durations[count++] = e->durations[op];
        }
        ++e->result.callbacks;
        if (e->hook(e->hook_context, e->now, server, count, e->candidate_ids,
                    e->candidate_durations, &chosen))
            return fail(e, "dispatch callback failed");
    } else if (e->policy == 1) {
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
    return schedule(e, e->now, START, chosen);
}

static int enqueue(ne_engine *e, uint32_t op) {
    uint32_t server = e->routes[op];
    e->queued[op] = e->now;
    e->next[op] = NONE;
    if (e->tail[server] == NONE) e->head[server] = op;
    else e->next[e->tail[server]] = op;
    e->tail[server] = op;
    if (record(e, 2, e->job_of[op], op, server)) return -1;
    return grant(e, server);
}

static int process(ne_engine *e, event ev) {
    uint32_t op = ev.id;
    if (ev.kind == ARRIVAL_BATCH) {
        uint32_t end = op;
        while (end < e->jobs && e->arrivals[end] == e->now) {
            if (record(e, 1, end, NONE, NONE) || wip_change(e, 1)) return -1;
            ++end;
        }
        if (end < e->jobs && schedule(e, e->arrivals[end], ARRIVAL_BATCH, end)) return -1;
        for (uint32_t job = op; job < end; ++job)
            if (enqueue(e, e->offsets[job])) return -1;
        return 0;
    }
    if (ev.kind == RELEASE_DISPATCH) return grant(e, op);
    uint32_t job = e->job_of[op], server = e->routes[op];
    if (ev.kind == START) {
        e->starts[op] = e->now;
        e->dispatch[e->dispatched++] = op;
        if (record(e, 3, job, op, server)) return -1;
        if (e->mode) {
            e->result.total_wait += e->now - e->queued[op];
        }
        double end = e->now + e->durations[op];
        if (!isfinite(end) || end <= e->now) return fail(e, "operation end time overflow or lost precision");
        return schedule(e, end, END, op);
    }
    if (ev.kind == END) {
        e->ends[op] = e->now;
        if (e->mode) {
            e->busy[server] += e->durations[op];
        }
        if (record(e, 4, job, op, server)) return -1;
        return schedule(e, e->now, ADVANCE, op);
    }
    e->occupied[server] = 0;
    if (schedule(e, e->now, RELEASE_DISPATCH, server)) return -1;
    if (op + 1 < e->offsets[job + 1]) return enqueue(e, op + 1);
    e->finished[job] = e->now;
    ++e->result.completed;
    if (e->mode) {
        e->result.total_flow += e->now - e->arrivals[job];
        double tardiness = e->now > e->due[job] ? e->now - e->due[job] : 0;
        e->result.total_tardiness += tardiness;
    }
    if (record(e, 5, job, NONE, NONE)) return -1;
    return wip_change(e, -1);
}

static int validate(ne_engine *e, const ne_input *in) {
    if (!in || !in->servers || in->servers == NONE || in->jobs == NONE || in->operations == NONE)
        return fail(e, "invalid input counts");
    if (!in->offsets || (in->jobs && (!in->arrivals || !in->due)) ||
        (in->operations && (!in->routes || !in->durations)))
        return fail(e, "missing input array");
    if (in->offsets[0] != 0 || in->offsets[in->jobs] != in->operations)
        return fail(e, "invalid operation offsets");
    for (uint32_t j = 0; j < in->jobs; ++j) {
        if (!isfinite(in->arrivals[j]) || in->arrivals[j] < 0 ||
            (j && in->arrivals[j] < in->arrivals[j - 1]) || !isfinite(in->due[j]))
            return fail(e, "invalid arrival or due date");
        uint32_t begin = in->offsets[j], end = in->offsets[j + 1];
        if (begin >= end || end > in->operations) return fail(e, "invalid job route offsets");
        for (uint32_t op = begin; op < end; ++op) {
            if (in->routes[op] >= in->servers || !isfinite(in->durations[op]) || in->durations[op] <= 0)
                return fail(e, "invalid operation server or duration");
            for (uint32_t previous = begin; previous < op; ++previous)
                if (in->routes[previous] == in->routes[op]) return fail(e, "repeated server in route");
        }
    }
    return 0;
}

ne_engine *ne_create(const ne_input *in, int policy, int mode, ne_hook hook, void *context,
                     const char *path, char *error, size_t error_capacity) {
    ne_engine *e = calloc(1, sizeof(*e));
    if (!e) {
        if (error && error_capacity) (void)snprintf(error, error_capacity, "engine allocation failed");
        return NULL;
    }
    if (validate(e, in)) goto failure;
    if ((policy != 0 && policy != 1) || mode < 0 || mode > 2 || (mode == 2 && (!path || !*path))) {
        (void)fail(e, "invalid policy, mode or record path");
        goto failure;
    }
    e->servers = in->servers;
    e->jobs = in->jobs;
    e->operations = in->operations;
    e->policy = policy;
    e->mode = mode;
    e->hook = hook;
    e->hook_context = context;
#define ALLOC(field, count) do { \
    e->field = allocate(e, (count), sizeof(*e->field)); \
    if (!e->field) goto failure; \
} while (0)
    ALLOC(arrivals, e->jobs); ALLOC(due, e->jobs); ALLOC(finished, e->jobs);
    ALLOC(offsets, (size_t)e->jobs + 1);
    ALLOC(durations, e->operations); ALLOC(routes, e->operations);
    ALLOC(job_of, e->operations); ALLOC(next, e->operations); ALLOC(queued, e->operations);
    ALLOC(starts, e->operations); ALLOC(ends, e->operations); ALLOC(dispatch, e->operations);
    ALLOC(head, e->servers); ALLOC(tail, e->servers); ALLOC(occupied, e->servers); ALLOC(busy, e->servers);
    if (hook) { ALLOC(candidate_ids, e->operations); ALLOC(candidate_durations, e->operations); }
    if (mode == 2) { ALLOC(record_buffer, RECORD_BUFFER_SIZE); }
#undef ALLOC
    if (e->jobs) {
        memcpy(e->arrivals, in->arrivals, e->jobs * sizeof(double));
        memcpy(e->due, in->due, e->jobs * sizeof(double));
    }
    memcpy(e->offsets, in->offsets, ((size_t)e->jobs + 1) * sizeof(uint32_t));
    if (e->operations) {
        memcpy(e->routes, in->routes, e->operations * sizeof(uint32_t));
        memcpy(e->durations, in->durations, e->operations * sizeof(double));
    }
    for (uint32_t j = 0; j < e->jobs; ++j)
        for (uint32_t op = e->offsets[j]; op < e->offsets[j + 1]; ++op) e->job_of[op] = j;
    for (uint32_t s = 0; s < e->servers; ++s) e->head[s] = e->tail[s] = NONE;
    e->result.starts = e->starts;
    e->result.ends = e->ends;
    e->result.finished = e->finished;
    e->result.dispatch = e->dispatch;
    e->result.busy = e->busy;
    if (mode == 2) {
        e->file = fopen(path, "wb");
        if (!e->file) { (void)fail(e, "cannot open record file"); goto failure; }
        /* Explicit buffer owns buffering; stdio is unbuffered to avoid a second buffer. */
        (void)setvbuf(e->file, NULL, _IONBF, 0);
        memcpy(e->record_buffer, "NEVT0001", 8);
        e->record_used = 8;
    }
    return e;
failure:
    if (error && error_capacity) (void)snprintf(error, error_capacity, "%s", e->error);
    ne_destroy(e);
    return NULL;
}

int ne_run(ne_engine *e) {
    if (!e) return -1;
    if (e->state != 0) return fail(e, "engine can only run once");
    e->state = 1;
    if (e->jobs && schedule(e, e->arrivals[0], ARRIVAL_BATCH, 0)) return -1;
    while (e->heap_size) {
        event ev = pop(e);
        e->now = ev.time;
        ++e->result.events;
        if (process(e, ev)) return -1;
    }
    if (e->result.completed != e->jobs || e->dispatched != e->operations)
        return fail(e, "incomplete simulation");
    if (e->mode == 2 && (flush_records(e) || fflush(e->file)))
        return fail(e, "record flush failed");
    e->result.makespan = e->now;
    e->state = 2;
    return 0;
}

const ne_result *ne_get_result(const ne_engine *e) { return e && e->state == 2 ? &e->result : NULL; }
const char *ne_error(const ne_engine *e) { return e ? e->error : "null engine"; }

void ne_destroy(ne_engine *e) {
    if (!e) return;
    if (e->file) { (void)flush_records(e); (void)fclose(e->file); }
    free(e->arrivals); free(e->due); free(e->durations); free(e->queued);
    free(e->offsets); free(e->routes); free(e->job_of); free(e->next);
    free(e->head); free(e->tail); free(e->occupied);
    free(e->starts); free(e->ends); free(e->finished); free(e->busy); free(e->dispatch);
    free(e->heap); free(e->candidate_ids); free(e->candidate_durations); free(e->record_buffer);
    free(e);
}
