#include "../csrc/dynamic_engine.h"
#include "../csrc/stock_kpi.h"

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
static int exercise(const char *path, size_t nth, int rich) {
    nd_test_fail_allocation(nth);
    char error[256];
    nd_engine *e = nd_create(8, 3, 1, path ? (rich ? 3 : 2) : 1, path, error, sizeof(error));
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
        if (j % 13 == 0 && nd_flush(e)) goto failed;
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

static int exercise_batch(const char *path, size_t nth) {
    nd_test_fail_allocation(nth);
    char error[256];
    nd_engine *e = nd_create(8, 3, 1, path ? 2 : 1, path, error, sizeof(error));
    if (!e) { assert(strstr(error, "allocation")); return -1; }
    double arrivals[33], dues[33], durations[264], at = 0;
    uint32_t offsets[34], routes[264], first;
    random_state = 981;
    for (uint32_t begin = 0; begin < 1025; begin += 33) {
        uint32_t jobs = 1025 - begin < 33 ? 1025 - begin : 33, operations = 0;
        offsets[0] = 0;
        for (uint32_t j = 0; j < jobs; ++j) {
            at += (random_u32() % 4) / 16.0;
            arrivals[j] = at;
            dues[j] = (double)(random_u32() % 100);
            uint32_t n = 1 + random_u32() % 8;
            for (uint32_t k = 0; k < n; ++k) {
                routes[operations] = (j + k) % 8;
                durations[operations++] = (1 + random_u32() % 32) / 16.0;
            }
            offsets[j + 1] = operations;
        }
        if (nd_advance(e, arrivals[0]) ||
            nd_add_many(e, jobs, arrivals, jobs, dues, jobs, offsets, jobs + 1,
                        routes, operations, durations, operations, &first)) goto failed;
        assert(first == begin);
        nd_snapshot snapshot;
        assert(!nd_inspect(e, 0, &snapshot));
    }
    if (nd_run(e)) goto failed;
    const nd_result *result = nd_get_result(e);
    assert(result && result->completed == 1025 && result->max_floor_wip <= 3);
    double floor = 0, flow = 0;
    for (uint32_t j = 0; j < 1025; ++j) {
        nd_snapshot snapshot;
        assert(!nd_inspect(e, j, &snapshot) && snapshot.state == 3);
        floor += snapshot.finished - snapshot.released;
        flow += snapshot.finished - snapshot.arrival;
    }
    assert(result->floor_wip_area == floor && result->wip_area == flow);
    nd_destroy(e);
    return 0;
failed:
    assert(strstr(nd_error(e), "allocation"));
    assert(!nd_get_result(e));
    nd_destroy(e);
    return -1;
}

static void batch_boundaries(void) {
    double arrivals[2] = {0, 0}, dues[2] = {1, 1}, durations[2] = {1, 1};
    uint32_t offsets[3] = {0, 1, 2}, routes[2] = {0, 1}, first;
    nd_engine *e;
    /* Validate all declared lengths before touching any input arrays. */
    for (unsigned field = 0; field < 5; ++field) {
        e = fresh();
        assert(nd_add_many(e, 2, (const double *)(uintptr_t)1, field == 0 ? 1 : 2,
                           (const double *)(uintptr_t)1, field == 1 ? 1 : 2,
                           (const uint32_t *)(uintptr_t)1, field == 2 ? 2 : 3,
                           (const uint32_t *)(uintptr_t)1, field == 3 ? 1 : 2,
                           (const double *)(uintptr_t)1, field == 4 ? 1 : 2, &first));
        nd_destroy(e);
    }
    e = fresh();
    assert(nd_add_many(e, UINT32_MAX, (const double *)(uintptr_t)1, UINT32_MAX,
                       dues, 2, offsets, 3, routes, 2, durations, 2, &first)); nd_destroy(e);
    for (unsigned field = 0; field < 6; ++field) {
        e = fresh();
        assert(nd_add_many(e, 2, field == 0 ? NULL : arrivals, 2, field == 1 ? NULL : dues, 2,
                           field == 2 ? NULL : offsets, 3, field == 3 ? NULL : routes, 2,
                           field == 4 ? NULL : durations, 2, field == 5 ? NULL : &first)); nd_destroy(e);
    }
    for (unsigned field = 0; field < 3; ++field) {
        uint32_t bad[3] = {0, 1, 2};
        bad[field] = field == 1 ? UINT32_MAX : 1;
        e = fresh();
        assert(nd_add_many(e, 2, arrivals, 2, dues, 2, bad, 3, routes, 2, durations, 2, &first)); nd_destroy(e);
    }
    e = fresh(); arrivals[1] = -1;
    assert(nd_add_many(e, 2, arrivals, 2, dues, 2, offsets, 3, routes, 2, durations, 2, &first));
    nd_snapshot snapshot;
    assert(nd_inspect(e, 0, &snapshot)); assert(!nd_get_result(e)); nd_destroy(e);
    e = fresh(); arrivals[1] = 0;
    nd_test_sequence(e, UINT64_MAX - 1);
    assert(nd_add_many(e, 2, arrivals, 2, dues, 2, offsets, 3, routes, 2, durations, 2, &first));
    nd_destroy(e);
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
    assert(nd_flush(NULL));
    e = fresh();
    assert(!nd_flush(e));
    assert(nd_now(e) == 0);
    assert(!nd_run(e));
    assert(!nd_flush(e));
    nd_destroy(e);
}

static void simultaneous(void) {
    const double arrivals[] = {5, 6, 6, 7};
    const uint32_t offsets[] = {0, 2, 5, 7, 8};
    const uint32_t routes[] = {1, 0, 0, 2, 1, 0, 1, 1};
    const double durations[] = {1, 2, 1, 2, 1, 1, 1, 1};
    const double releases[] = {5, 6, 9, 10};
    const double finishes[] = {9, 10, 11, 12};
    char error[256];
    nd_test_fail_allocation(0);
    for (int policy = 0; policy < 2; ++policy) {
        nd_engine *e = nd_create(3, 2, policy, 1, NULL, error, sizeof(error));
        assert(e);
        for (uint32_t j = 0; j < 4; ++j) {
            uint32_t first = offsets[j], n = offsets[j + 1] - first, id;
            assert(!nd_advance(e, arrivals[j]));
            assert(!nd_add(e, arrivals[j], 10, n, routes + first, n, durations + first, n, &id));
            assert(id == j);
        }
        assert(!nd_run(e));
        const nd_result *result = nd_get_result(e);
        assert(result && result->completed == 4);
        for (uint32_t j = 0; j < 4; ++j) {
            assert(result->released[j] == releases[j]);
            assert(result->finished[j] == finishes[j]);
        }
        assert(result->starts[6] == 10 && result->starts[7] == 11);
        nd_destroy(e);
    }
}

static int exercise_stock(size_t nth) {
    nd_test_fail_allocation(nth);
    char error[256];
    nd_engine *e = nd_create(3, 2, 1, 0, NULL, error, sizeof(error));
    if (!e) { assert(strstr(error, "allocation")); return -1; }
    if (nd_enable_stock_kpis(e, 0.25)) goto failed;
    for (uint32_t j = 0; j < 127; ++j) {
        uint32_t routes[3] = {j % 3, (j + 1) % 3, (j + 2) % 3}, id;
        double durations[3] = {0.1, 0.2, 0.3};
        double arrival = (double)j / 100;
        if (nd_advance(e, arrival) || nd_add(e, arrival, 1, 3, routes, 3, durations, 3, &id)) goto failed;
    }
    if (nd_run(e)) goto failed;
    nd_test_fail_allocation(0);
    nd_kpi_snapshot snapshot;
    assert(!nd_stock_kpis(e, &snapshot) && snapshot.present == 255 && snapshot.closed);
    assert(nd_get_result(e)->records == 0);
    nd_destroy(e);
    return 0;
failed:
    assert(strstr(nd_error(e), "allocation"));
    nd_kpi_snapshot invalid;
    assert(nd_stock_kpis(e, &invalid) == -1);
    nd_destroy(e);
    return -1;
}
static void stock_boundaries(void) {
    nd_engine *e = fresh();
    nd_kpi_snapshot snapshot, expected;
    assert(nd_stock_kpis(e, &snapshot) == -1);
    assert(!nd_enable_stock_kpis(e, 0));
    assert(!nd_stock_kpis(e, &snapshot) && !snapshot.present && snapshot.closed);
    uint32_t route = 0, id;
    double duration = 1;
    assert(!nd_add(e, 0, 0, 1, &route, 1, &duration, 1, &id));
    assert(!nd_advance(e, 0.5));
    assert(!nd_stock_kpis(e, &expected));
    for (size_t nth = 1; nth <= 2; ++nth) {
        nd_test_fail_allocation(nth);
        assert(nd_stock_kpis(e, &snapshot) == -2);
        nd_test_fail_allocation(0);
        assert(!nd_stock_kpis(e, &snapshot));
        assert(!memcmp(&snapshot, &expected, sizeof(snapshot)));
    }
    assert(!nd_run(e) && !nd_stock_kpis(e, &snapshot));
    assert(snapshot.present == 255 && snapshot.values[0] == 1 && snapshot.values[6] == 0.5);
    nd_destroy(e);
    for (unsigned i = 0; i < 4; ++i) {
        e = fresh();
        if (i == 0) assert(nd_enable_stock_kpis(e, NAN));
        if (i == 1) assert(nd_enable_stock_kpis(e, -1));
        if (i == 2) { assert(!nd_advance(e, 1)); assert(nd_enable_stock_kpis(e, 0)); }
        if (i == 3) { assert(!nd_enable_stock_kpis(e, 0)); assert(nd_enable_stock_kpis(e, 0)); }
        assert(nd_stock_kpis(e, &snapshot) == -1);
        nd_destroy(e);
    }
    /* Expansion allocation/growth failures own their original storage. */
    for (size_t nth = 1; nth <= 3; ++nth) {
        nd_sum sum = {0};
        nd_test_fail_allocation(nth);
        int rc = 0;
        for (unsigned i = 0; i < 20 && !rc; ++i) rc = nd_sum_add(&sum, ldexp(1, -(int)(54 * i)));
        assert(rc);
        nd_sum_destroy(&sum);
    }
    nd_test_fail_allocation(0);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    stock_boundaries();
    assert(exercise_stock(0) == 0);
    size_t stock_failure = 1;
    while (exercise_stock(stock_failure)) { assert(stock_failure < 512); ++stock_failure; }
    printf("stock KPI sanitizer passed: %zu mutating allocation failures, read-only snapshot failures\n", stock_failure - 1);
    boundaries();
    batch_boundaries();
    simultaneous();
    assert(exercise(NULL, 0, 0) == 0);
    size_t failure = 1;
    while (exercise(argv[1], failure, 0)) {
        assert(failure < 512);
        ++failure;
    }
    printf("dynamic sanitizer passed: %zu allocation failure positions, randomized growth and boundary cases\n", failure - 1);
    failure = 1;
    while (exercise(argv[1], failure, 1)) {
        assert(failure < 512);
        ++failure;
    }
    printf("rich recording sanitizer passed: %zu allocation failure positions\n", failure - 1);
    assert(exercise_batch(NULL, 0) == 0);
    failure = 1;
    while (exercise_batch(argv[1], failure)) {
        assert(failure < 512);
        ++failure;
    }
    printf("bulk sanitizer passed: %zu allocation failure positions, 1025 copied jobs and boundary cases\n", failure - 1);
    return 0;
}
