#ifndef ND_INTERNAL_H
#define ND_INTERNAL_H
/* Shared private storage layout. No Python or observer callbacks enter here. */
#include "dynamic_engine.h"
#include "stock_kpi.h"
#include <stdio.h>
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
    nd_stock_state *stock;
    int stock_horizon;
    nd_result result;
    char error[256];
};

typedef int (*nd_completion_callback)(void *, uint32_t);
int nd_internal_fail(nd_engine *, const char *);
int nd_internal_advance(nd_engine *, double, nd_completion_callback, void *);
int nd_internal_run(nd_engine *, uint64_t forgotten, nd_completion_callback, void *);
int nd_internal_event_compare(const void *, const void *);
#ifdef ND_TEST_ALLOCATIONS
size_t nd_internal_test_attempts(void);
#endif
#endif
