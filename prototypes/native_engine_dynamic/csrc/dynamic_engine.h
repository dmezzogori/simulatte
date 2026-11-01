#ifndef DYNAMIC_ENGINE_H
#define DYNAMIC_ENGINE_H
#include <stddef.h>
#include <stdint.h>

typedef struct nd_engine nd_engine;
typedef struct {
    const double *starts, *ends, *finished, *released;
    const uint32_t *dispatch;
    double makespan;
    uint64_t completed, events, callbacks, records;
    double total_flow, total_wait, total_tardiness;
    const double *busy;
    double wip_area;
    uint64_t max_wip;
    double total_pool_wait, floor_wip_area;
    uint64_t max_floor_wip;
} nd_result;
typedef struct { double arrival, released, finished; uint32_t state; } nd_snapshot;
/* cap == 0 means unlimited. Every failed mutating call poisons the engine.
 * Caller supplies readable arrays of exactly the declared lengths; lengths are
 * checked against count before either array is read. IDs exclude UINT32_MAX.
 * Returned result pointers are engine-owned, valid until nd_destroy only. */
nd_engine *nd_create(uint32_t servers, uint32_t cap, int policy, int mode,
                     const char *path, char *error, size_t error_capacity);
int nd_add(nd_engine *, double arrival, double due, uint32_t count,
           const uint32_t *routes, size_t route_length,
           const double *durations, size_t duration_length, uint32_t *id);
int nd_advance(nd_engine *, double until);
int nd_run(nd_engine *);
int nd_inspect(const nd_engine *, uint32_t id, nd_snapshot *);
double nd_now(const nd_engine *);
const nd_result *nd_get_result(const nd_engine *);
const char *nd_error(const nd_engine *);
void nd_destroy(nd_engine *);
#ifdef ND_TEST_ALLOCATIONS
void nd_test_fail_allocation(size_t nth);
void nd_test_sequence(nd_engine *, uint64_t value);
#endif
#endif
