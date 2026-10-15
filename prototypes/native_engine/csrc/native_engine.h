#ifndef NATIVE_ENGINE_H
#define NATIVE_ENGINE_H

#include <stddef.h>
#include <stdint.h>

/* Borrowed input arrays have their indicated lengths; create validates and copies.
 * offsets has jobs+1 elements; routes and durations each have operations elements.
 * Counts/IDs exclude UINT32_MAX, which is the absent-ID sentinel. */
typedef struct {
    uint32_t servers, jobs, operations;
    const double *arrivals, *due;
    const uint32_t *offsets, *routes;
    const double *durations;
} ne_input;

/* Return zero and write a supplied candidate ID, or nonzero to abort the run.
 * Candidate arrays are borrowed for the duration of this synchronous call only.
 * A callback must not destroy or mutate its calling engine. */
typedef int (*ne_hook)(void *, double, uint32_t, size_t,
                       const uint32_t *, const double *, uint32_t *);

typedef struct ne_engine ne_engine;
typedef struct {
    const double *starts, *ends, *finished;
    const uint32_t *dispatch;
    double makespan;
    uint64_t completed, events, callbacks, records;
    double total_flow, total_wait, total_tardiness;
    const double *busy;
    double wip_area;
    uint64_t max_wip;
} ne_result;

/* policy: 0 FIFO, 1 SPT. mode: 0 none, 1 KPI, 2 record (requires path).
 * All errors are copied into error when error_capacity permits; no global state. */
ne_engine *ne_create(const ne_input *, int policy, int mode, ne_hook, void *,
                     const char *path, char *error, size_t error_capacity);
int ne_run(ne_engine *);
const ne_result *ne_get_result(const ne_engine *);
const char *ne_error(const ne_engine *);
void ne_destroy(ne_engine *);

#ifdef NE_TEST_ALLOCATIONS
/* Standalone harness only: fail the nth allocation after this reset (0 disables).
 * Not present in the release library; this test switch is deliberately global. */
void ne_test_fail_allocation(size_t nth);
#endif

#endif
