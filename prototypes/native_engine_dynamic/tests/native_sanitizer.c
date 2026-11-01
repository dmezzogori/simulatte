#include "../csrc/dynamic_engine.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t random_state = 2398;
static uint32_t random_u32(void) {
    random_state = random_state * 1664525u + 1013904223u;
    return random_state;
}

/* Return zero only when the injected allocation did not interrupt the whole
 * workload. Every error path must remain inspectable as failed and destroyable. */
static int exercise(const char *path, size_t nth) {
    nd_test_fail_allocation(nth);
    char error[256];
    nd_engine *e = nd_create(8, 3, 1, path ? 2 : 1, path, error, sizeof(error));
    if (!e) { assert(strstr(error, "allocation")); return -1; }
    uint32_t routes[8], id = 0;
    double durations[8];
    double arrival = 0;
    random_state = 2398;
    for (uint32_t j = 0; j < 1025; ++j) {
        uint32_t n = 1 + random_u32() % 8;
        for (uint32_t k = 0; k < n; ++k) {
            routes[k] = (k + j) % 8;
            durations[k] = (1 + random_u32() % 32) / 16.0;
        }
        arrival += (random_u32() % 4) / 16.0;
        if (j % 13 == 0 && nd_advance(e, arrival)) goto failed;
        if (nd_add(e, arrival, (double)(random_u32() % 100), n, routes, n, durations, n, &id)) goto failed;
        assert(id == j);
        nd_snapshot snapshot;
        assert(nd_inspect(e, 0, &snapshot) == 0);
        assert(snapshot.arrival >= 0);
    }
    if (nd_run(e)) goto failed;
    const nd_result *result = nd_get_result(e);
    assert(result && result->completed == 1025 && result->callbacks == 0);
    assert(result->max_floor_wip <= 3);
    double total_floor = 0, total_flow = 0;
    for (uint32_t j = 0; j < 1025; ++j) {
        nd_snapshot snapshot;
        assert(nd_inspect(e, j, &snapshot) == 0 && snapshot.state == 3);
        assert(snapshot.finished >= snapshot.released && snapshot.released >= snapshot.arrival);
        total_floor += snapshot.finished - snapshot.released;
        total_flow += snapshot.finished - snapshot.arrival;
    }
    assert(result->floor_wip_area == total_floor);
    assert(result->wip_area == total_flow);
    nd_destroy(e);
    return 0;
failed:
    assert(strstr(nd_error(e), "allocation"));
    assert(nd_get_result(e) == NULL);
    nd_snapshot snapshot;
    assert(nd_inspect(e, 0, &snapshot) != 0);
    nd_destroy(e);
    return -1;
}

static nd_engine *fresh(void) {
    char error[256];
    nd_test_fail_allocation(0);
    nd_engine *e = nd_create(2, 0, 0, 0, NULL, error, sizeof(error));
    assert(e);
    return e;
}

static void boundaries(void) {
    char error[256];
    uint32_t route[2] = {0, 1}, id;
    double duration[2] = {1, 1};
    nd_engine *e;
    assert(!nd_create(0, 0, 0, 0, NULL, error, sizeof(error)));
    assert(!nd_create(UINT32_MAX, 0, 0, 0, NULL, error, sizeof(error)));
    assert(!nd_create(1, UINT32_MAX, 0, 0, NULL, error, sizeof(error)));
    assert(!nd_create(1, 0, 0, 0, "unexpected", error, sizeof(error)));
    /* Mismatched lengths/counts are rejected before touching intentionally
     * unreadable nonnull pointers; sanitizer must see no dereference. */
    e = fresh();
    assert(nd_add(e, 0, 1, 2, (const uint32_t *)(uintptr_t)1, 1,
                  (const double *)(uintptr_t)1, 2, &id));
    nd_destroy(e);
    e = fresh();
    assert(nd_add(e, 0, 1, UINT32_MAX, (const uint32_t *)(uintptr_t)1, UINT32_MAX,
                  (const double *)(uintptr_t)1, UINT32_MAX, &id));
    nd_destroy(e);
    e = fresh(); assert(nd_add(e, 0, 1, 1, NULL, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh(); assert(nd_add(e, 0, 1, 1, route, 1, NULL, 1, &id)); nd_destroy(e);
    e = fresh(); assert(nd_add(e, 0, 1, 1, route, 1, duration, 1, NULL)); nd_destroy(e);
    e = fresh(); assert(nd_add(e, NAN, 1, 1, route, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh(); assert(nd_add(e, 0, INFINITY, 1, route, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh(); assert(nd_advance(e, INFINITY)); nd_destroy(e);
    e = fresh(); assert(nd_advance(e, -1)); nd_destroy(e);
    e = fresh(); route[0] = 2;
    assert(nd_add(e, 0, 1, 1, route, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh(); route[0] = route[1] = 0;
    assert(nd_add(e, 0, 1, 2, route, 2, duration, 2, &id)); nd_destroy(e);
    e = fresh(); duration[0] = 0;
    assert(nd_add(e, 0, 1, 1, route, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh(); duration[0] = 1e308;
    assert(!nd_add(e, 1e308, 1, 1, route, 1, duration, 1, &id));
    assert(nd_run(e)); assert(!nd_get_result(e)); nd_destroy(e);
    e = fresh(); duration[0] = 1;
    nd_test_sequence(e, UINT64_MAX);
    assert(nd_add(e, 0, 1, 1, route, 1, duration, 1, &id)); nd_destroy(e);
    e = fresh();
    assert(!nd_add(e, 0, 1, 1, route, 1, duration, 1, &id));
    nd_test_sequence(e, UINT64_MAX);
    assert(nd_run(e)); nd_destroy(e);
    e = fresh();
    assert(!nd_run(e)); assert(nd_get_result(e)->completed == 0); nd_destroy(e);
    e = fresh();
    assert(!nd_add(e, 0, 1, 1, route, 1, duration, 1, &id));
    assert(!nd_advance(e, 2));
    assert(!nd_add(e, 2, 1, 1, route, 1, duration, 1, &id));
    assert(!nd_run(e)); assert(nd_get_result(e)->completed == 2); nd_destroy(e);
    nd_destroy(NULL);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    boundaries();
    assert(exercise(NULL, 0) == 0);
    size_t failure = 1;
    while (exercise(argv[1], failure)) {
        assert(failure < 512);
        ++failure;
    }
    printf("dynamic sanitizer passed: %zu allocation failure positions, randomized growth and boundary cases\n", failure - 1);
    return 0;
}
