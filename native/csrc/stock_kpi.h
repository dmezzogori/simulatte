#ifndef ND_STOCK_KPI_H
#define ND_STOCK_KPI_H
#include "dynamic_engine.h"

/* Research-internal accumulators, not a public Simulatte API. */
typedef struct { double *p; size_t n, capacity; double special; } nd_sum;
int nd_sum_add(nd_sum *, double);
double nd_sum_value(const nd_sum *);
void nd_sum_clear(nd_sum *);
void nd_sum_destroy(nd_sum *);
typedef struct nd_stock_state nd_stock_state;
nd_stock_state *nd_stock_create(double warmup);
void nd_stock_destroy(nd_stock_state *);
int nd_stock_busy(nd_stock_state *, double now, int delta);
int nd_stock_floor(nd_stock_state *, double now, int delta);
int nd_stock_finish(nd_stock_state *, double now, double arrival, double due,
                    uint32_t count, const double *ends, const double *queued, const double *durations);
int nd_stock_snapshot(const nd_stock_state *, double now, uint32_t servers, int horizon, nd_kpi_snapshot *);
#ifdef ND_TEST_ALLOCATIONS
void *nd_test_calloc(size_t, size_t);
void *nd_test_realloc(void *, size_t);
#endif
#endif
