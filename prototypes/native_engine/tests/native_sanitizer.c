/* Standalone executable: ASan/UBSan never need to be loaded into Python. */
#include "../csrc/native_engine.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { size_t calls; int policy, invalid, fail; } hook_state;

static int hook(void *context, double now, uint32_t server, size_t count,
                const uint32_t *ids, const double *durations, uint32_t *chosen) {
    hook_state *state = context;
    assert(isfinite(now));
    assert(server < 3);
    assert(count > 0);
    ++state->calls;
    if (state->fail) return 1;
    if (state->invalid) { *chosen = UINT32_MAX; return 0; }
    size_t selected = 0;
    if (state->policy)
        for (size_t i = 1; i < count; ++i)
            if (durations[i] < durations[selected]) selected = i;
    *chosen = ids[selected];
    return 0;
}

static ne_engine *create(const ne_input *in, int policy, int mode, hook_state *state, const char *path) {
    char error[256];
    ne_engine *e = ne_create(in, policy, mode, state ? hook : NULL, state, path, error, sizeof(error));
    if (!e) { fprintf(stderr, "unexpected create failure: %s\n", error); abort(); }
    return e;
}

static void rejected(const ne_input *in) {
    char error[256] = {0};
    ne_engine *e = ne_create(in, 0, 0, NULL, NULL, NULL, error, sizeof(error));
    assert(!e && error[0]);
}

static void boundaries(const char *path) {
    double arrivals[] = {0, 1, 1}, due[] = {2, 4, 6}, durations[] = {3, 2, 1};
    uint32_t offsets[] = {0, 1, 2, 3}, routes[] = {0, 0, 0};
    ne_input in = {1, 3, 3, arrivals, due, offsets, routes, durations};
    for (int policy = 0; policy < 2; ++policy) {
        for (int mode = 0; mode < 3; ++mode) {
            for (int hooked = 0; hooked < 2; ++hooked) {
                hook_state state = {0, policy, 0, 0};
                ne_engine *e = create(&in, policy, mode, hooked ? &state : NULL, path);
                assert(ne_get_result(e) == NULL);
                assert(ne_run(e) == 0);
                const ne_result *r = ne_get_result(e);
                assert(r && r->completed == 3 && r->makespan == 6);
                assert(r->starts[0] == 0 && r->starts[1] == (policy ? 4 : 3));
                assert(r->starts[2] == (policy ? 3 : 5));
                assert(r->callbacks == (hooked ? 3 : 0));
                assert(r->records == (mode == 2 ? 15 : 0));
                if (mode) {
                    assert(r->total_flow == (policy ? 11 : 12));
                    assert(r->total_flow == r->wip_area && r->max_wip == 3);
                }
                assert(ne_run(e) != 0);
                ne_destroy(e);
            }
        }
    }
    hook_state bad = {0, 0, 1, 0};
    ne_engine *e = create(&in, 0, 2, &bad, path);
    assert(ne_run(e) != 0 && strstr(ne_error(e), "noncandidate"));
    assert(ne_get_result(e) == NULL);
    ne_destroy(e);
    bad.invalid = 0; bad.fail = 1;
    e = create(&in, 0, 0, &bad, NULL);
    assert(ne_run(e) != 0 && strstr(ne_error(e), "callback failed"));
    ne_destroy(e);
    e = create(&in, 0, 2, NULL, path);
    ne_destroy(e); /* never run */
    ne_destroy(NULL);
    assert(ne_run(NULL) != 0 && ne_get_result(NULL) == NULL);

    /* C owns a copy, including input offsets, routes and durations. */
    e = create(&in, 0, 0, NULL, NULL);
    durations[0] = 100; routes[0] = 999; arrivals[0] = 999; offsets[1] = 999;
    assert(ne_run(e) == 0 && ne_get_result(e)->makespan == 6);
    ne_destroy(e);
    durations[0] = 3; routes[0] = 0; arrivals[0] = 0; offsets[1] = 1;

    in.servers = 0; rejected(&in); in.servers = 1;
    offsets[0] = 1; rejected(&in); offsets[0] = 0;
    offsets[1] = 0; rejected(&in); offsets[1] = 1;
    offsets[3] = 4; rejected(&in); offsets[3] = 3;
    routes[0] = 1; rejected(&in); routes[0] = 0;
    durations[0] = 0; rejected(&in);
    durations[0] = NAN; rejected(&in);
    durations[0] = INFINITY; rejected(&in); durations[0] = 3;
    arrivals[0] = -1; rejected(&in);
    arrivals[0] = NAN; rejected(&in);
    arrivals[0] = 2; rejected(&in); arrivals[0] = 0;
    due[0] = INFINITY; rejected(&in); due[0] = 2;
    const double *saved = in.durations;
    in.durations = NULL; rejected(&in); in.durations = saved;
    rejected(NULL);
    in.jobs = 1; offsets[1] = 3;
    rejected(&in); /* repeated server in one route */

    in.operations = 1; offsets[1] = 1;
    arrivals[0] = 1e308; durations[0] = 1e308;
    e = create(&in, 0, 0, NULL, NULL);
    assert(ne_run(e) != 0); ne_destroy(e);
    durations[0] = 1;
    e = create(&in, 0, 0, NULL, NULL);
    assert(ne_run(e) != 0); ne_destroy(e);

    in.jobs = in.operations = 0;
    e = create(&in, 0, 2, NULL, path);
    assert(ne_run(e) == 0 && ne_get_result(e)->makespan == 0);
    ne_destroy(e);
}

static uint32_t random_state = 19;
static uint32_t random_value(void) {
    random_state = random_state * 1664525u + 1013904223u;
    return random_state;
}

static void random_workloads(const char *path) {
    enum { JOBS = 1024, OPS = JOBS * 3 };
    double *arrivals = calloc(JOBS, sizeof(double)), *due = calloc(JOBS, sizeof(double));
    double *durations = calloc(OPS, sizeof(double));
    uint32_t *offsets = calloc(JOBS + 1, sizeof(uint32_t)), *routes = calloc(OPS, sizeof(uint32_t));
    assert(arrivals && due && durations && offsets && routes);
    ne_input in = {3, JOBS, OPS, arrivals, due, offsets, routes, durations};
    for (unsigned round = 0; round < 120; ++round) {
        double now = 0;
        for (uint32_t j = 0; j < JOBS; ++j) {
            now += (double)(random_value() % 3);
            arrivals[j] = now;
            due[j] = now + 12;
            offsets[j] = 3 * j;
            uint32_t first = random_value() % 3;
            for (uint32_t k = 0; k < 3; ++k) {
                routes[3 * j + k] = (first + k) % 3;
                durations[3 * j + k] = (double)(1 + random_value() % 7);
            }
        }
        offsets[JOBS] = OPS;
        int policy = (int)(round % 2);
        hook_state state = {0, policy, 0, 0};
        ne_engine *plain = create(&in, policy, 0, NULL, NULL);
        ne_engine *observed = create(&in, policy, 2, &state, path);
        assert(ne_run(plain) == 0 && ne_run(observed) == 0);
        const ne_result *a = ne_get_result(plain), *b = ne_get_result(observed);
        assert(a->completed == JOBS && b->completed == JOBS && b->callbacks == OPS);
        assert(memcmp(a->starts, b->starts, OPS * sizeof(double)) == 0);
        assert(memcmp(a->ends, b->ends, OPS * sizeof(double)) == 0);
        assert(memcmp(a->finished, b->finished, JOBS * sizeof(double)) == 0);
        assert(memcmp(a->dispatch, b->dispatch, OPS * sizeof(uint32_t)) == 0);
        assert(b->total_flow == b->wip_area);
        ne_destroy(plain); ne_destroy(observed);
    }
    /* Inject allocation failures across every constructor allocation and each
     * heap growth; assert failures remain cleanly destroyable under ASan. */
    for (size_t nth = 1; nth < 50; ++nth) {
        ne_test_fail_allocation(nth);
        char error[256];
        hook_state state = {0, 0, 0, 0};
        ne_engine *e = ne_create(&in, 0, 2, hook, &state, path, error, sizeof(error));
        if (!e) assert(strstr(error, "allocation"));
        else {
            if (ne_run(e)) assert(strstr(ne_error(e), "allocation"));
            ne_destroy(e);
        }
    }
    ne_test_fail_allocation(0);
    free(arrivals); free(due); free(durations); free(offsets); free(routes);
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s scratch-trace-path\n", argv[0]); return 2; }
    boundaries(argv[1]);
    random_workloads(argv[1]);
    assert(remove(argv[1]) == 0);
    puts("native sanitizer checks passed: boundaries, 120 randomized lifecycles, allocation faults");
    return 0;
}
