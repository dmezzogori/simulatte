#ifndef ND_RETENTION_H
#define ND_RETENTION_H
#include "dynamic_engine.h"
typedef struct bs_engine bs_engine;
typedef nd_storage_stats bs_stats;
bs_engine *bs_create(uint32_t servers, uint32_t cap, int policy, int bounded,
                     double warmup, char *error, size_t capacity);
int bs_set_capacities(bs_engine *, const uint32_t *, size_t count);
int bs_add(bs_engine *, double arrival, double due, uint32_t count,
           const uint32_t *routes, const double *durations, uint64_t *identity);
int bs_advance(bs_engine *, double until);
int bs_run(bs_engine *);
int bs_inspect(const bs_engine *, uint64_t id, nd_snapshot *);
int bs_kpis(const bs_engine *, nd_kpi_snapshot *);
int bs_state(bs_engine *, const unsigned char **, size_t *);
int bs_completions(const bs_engine *, const unsigned char **, size_t *);
int bs_statistics(const bs_engine *, bs_stats *);
double bs_now(const bs_engine *);
const char *bs_error(const bs_engine *);
void bs_destroy(bs_engine *);
#ifdef ND_TEST_ALLOCATIONS
size_t bs_test_attempts(void);
void bs_test_identity(bs_engine *, uint64_t);
#endif

#endif
