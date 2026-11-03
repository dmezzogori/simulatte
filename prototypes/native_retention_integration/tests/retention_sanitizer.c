#include "../../../native/csrc/retention.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int exercise(size_t failure, int bounded) {
    nd_test_fail_allocation(failure);
    char error[256];
    bs_engine *b = bs_create(3, 3, 1, bounded, 2, error, sizeof(error));
    if (!b) { assert(strstr(error, "allocation")); return 1; }
    int temporary_failure = 0;
    for (uint32_t j = 0; j < 256; ++j) {
        if (j % 8 == 0 && bs_advance(b, j * 0.5)) goto failed;
        uint32_t route[] = {j % 3, (j + 1) % 3, (j + 2) % 3};
        double durations[] = {0.125, 0.25, 0.375}; uint64_t id;
        if (bs_add(b, j * 0.5, j, 3, route, durations, &id)) goto failed;
        assert(id == j);
        nd_snapshot s;
        assert(bs_inspect(b, id, &s) == 0 && s.arrival == j * 0.5);
        if (j % 16 == 0) {
            const unsigned char *data; size_t size;
            int rc = bs_state(b, &data, &size);
            if (rc == -2) { temporary_failure = 1; assert(bs_inspect(b, id, &s) == 0); }
            else assert(!rc && size > 100 && !memcmp(data, "NBST0001", 8));
            nd_kpi_snapshot kpi;
            rc = bs_kpis(b, &kpi);
            if (rc == -2) temporary_failure = 1;
            else assert(rc == 0);
        }
    }
    if (bs_run(b)) goto failed;
    bs_stats stats;
    assert(bs_statistics(b, &stats) == 0 && stats.completed == 256 && stats.admitted == 256);
    assert(stats.jobs == (bounded ? 0 : 256));
    assert(!bounded || stats.job_capacity <= 16);
    bs_destroy(b);
    return temporary_failure;
failed:
    assert(strstr(bs_error(b), "allocation"));
    { bs_stats stats; nd_snapshot snapshot; nd_kpi_snapshot kpi;
      assert(bs_statistics(b, &stats) != 0 && bs_kpis(b, &kpi) != 0 && bs_inspect(b, 0, &snapshot) != 0); }
    bs_destroy(b); return 1;
}
static void readonly_failures(void) {
    char error[256]; nd_test_fail_allocation(0);
    bs_engine *b = bs_create(3, 1, 1, 1, 0, error, sizeof(error)); assert(b);
    uint32_t route[] = {0, 1}; double d[] = {10, 1}; uint64_t id;
    assert(!bs_add(b, 0, 20, 2, route, d, &id));
    assert(!bs_add(b, 0, 20, 2, route, d, &id));
    assert(!bs_advance(b, 1));
    const unsigned char *data; size_t size;
    assert(!bs_state(b, &data, &size));
    unsigned char *copy = malloc(size); assert(copy); memcpy(copy, data, size);
    for (size_t n = 1; n <= 2; ++n) {
        nd_test_fail_allocation(n);
        assert(bs_state(b, &data, &size) == -2);
        nd_test_fail_allocation(0);
        assert(!bs_state(b, &data, &size) && !memcmp(copy, data, size));
    }
    free(copy); bs_destroy(b);
}
static void identities(void) {
    char error[256]; nd_test_fail_allocation(0);
    bs_engine *b = bs_create(1, 0, 0, 1, 0, error, sizeof(error)); assert(b);
    uint32_t route[] = {0}; double d[] = {0.25}; uint64_t id;
    for (uint32_t j = 0; j < 10000; ++j) {
        assert(!bs_add(b, j, j, 1, route, d, &id) && id == j);
        assert(!bs_advance(b, j + 1));
        nd_snapshot s;
        assert(bs_inspect(b, id, &s) == -3 && bs_inspect(b, 0, &s) == -3);
    }
    bs_test_identity(b, UINT64_MAX);
    assert(bs_add(b, 10000, 10000, 1, route, d, &id) != 0);
    assert(strstr(bs_error(b), "identity overflow"));
    bs_destroy(b);
}
int main(void) {
    for (int bounded = 0; bounded <= 1; ++bounded) {
        assert(exercise(0, bounded) == 0);
        size_t calls = bs_test_attempts();
        size_t n = 1;
        for (; n <= calls + 1; ++n) if (!exercise(n, bounded)) break;
        assert(n == calls + 1);
        printf("%s allocation-failure positions: %zu\n", bounded ? "bounded" : "full", calls);
    }
    readonly_failures(); identities();
    puts("streaming ASan/UBSan checks pass");
    return 0;
}
