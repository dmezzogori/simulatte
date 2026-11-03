#include "stock_kpi.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifdef ND_TEST_ALLOCATIONS
#define calloc nd_test_calloc
#define realloc nd_test_realloc
#endif

/* Match simulatte.kpi._ExactSum, including its non-finite handling. The
 * expansion and final half-even correction follow CPython's math.fsum:
 * https://github.com/python/cpython/blob/v3.14.0/Modules/mathmodule.c
 * Compile without fast-math or contraction; each binary64 rounding matters. */
int nd_sum_add(nd_sum *s, double x) {
    if (!isfinite(x)) { s->special += x; return 0; }
    if (s->n == s->capacity) {
        size_t cap = s->capacity ? s->capacity * 2 : 8;
        if (cap < s->capacity || cap > SIZE_MAX / sizeof(double)) return -1;
        double *p = realloc(s->p, cap * sizeof(double));
        if (!p) return -1;
        s->p = p; s->capacity = cap;
    }
    size_t i = 0;
    for (size_t j = 0; j < s->n; ++j) {
        double y = s->p[j];
        if (fabs(x) < fabs(y)) { double tmp = x; x = y; y = tmp; }
        double hi = x + y;
        if (!isfinite(hi)) { s->n = i; s->special += hi; return 0; }
        double lo = y - (hi - x);
        if (lo != 0) s->p[i++] = lo;
        x = hi;
    }
    s->p[i++] = x; s->n = i;
    return 0;
}
double nd_sum_value(const nd_sum *s) {
    size_t n = s->n;
    double hi = 0, lo = 0;
    if (n) {
        hi = s->p[--n];
        while (n) {
            double x = hi, y = s->p[--n];
            hi = x + y;
            lo = y - (hi - x);
            if (lo != 0) break;
        }
        if (n && ((lo < 0 && s->p[n - 1] < 0) || (lo > 0 && s->p[n - 1] > 0))) {
            double y = lo * 2, x = hi + y;
            if (y == x - hi) hi = x;
        }
    }
    return s->special == 0 ? hi : s->special + hi;
}
void nd_sum_clear(nd_sum *s) { s->n = 0; s->special = 0; }
void nd_sum_destroy(nd_sum *s) { free(s->p); }

typedef struct { nd_sum area; double t; uint64_t value; } nd_signal;
struct nd_stock_state {
    double warmup;
    uint64_t completed, tardy;
    nd_sum flow, late, tardiness, wait, scratch;
    nd_signal busy, floor;
};
nd_stock_state *nd_stock_create(double warmup) {
    nd_stock_state *s = calloc(1, sizeof(*s));
    if (s) { s->warmup = warmup; s->busy.t = s->floor.t = warmup; }
    return s;
}
void nd_stock_destroy(nd_stock_state *s) {
    if (!s) return;
    nd_sum_destroy(&s->flow); nd_sum_destroy(&s->late); nd_sum_destroy(&s->tardiness);
    nd_sum_destroy(&s->wait); nd_sum_destroy(&s->scratch);
    nd_sum_destroy(&s->busy.area); nd_sum_destroy(&s->floor.area);
    free(s);
}
static int update(nd_signal *s, double now, int delta) {
    if (now > s->t) {
        double term = (double)s->value * (now - s->t);
        if (nd_sum_add(&s->area, term)) return -1;
        s->t = now;
    }
    if (delta > 0) ++s->value; else --s->value;
    return 0;
}
int nd_stock_busy(nd_stock_state *s, double now, int delta) { return update(&s->busy, now, delta); }
int nd_stock_floor(nd_stock_state *s, double now, int delta) { return update(&s->floor, now, delta); }
int nd_stock_finish(nd_stock_state *s, double now, double arrival, double due,
                    uint32_t count, const double *ends, const double *queued, const double *durations) {
    if (now < s->warmup) return 0;
    nd_sum_clear(&s->scratch);
    for (uint32_t i = 0; i < count; ++i) {
        /* ProductionJob.queue_time uses exit - entry - planned duration,
         * not start - entry (different rounding for continuous times). */
        double wait = (ends[i] - queued[i]) - durations[i];
        if (nd_sum_add(&s->scratch, wait)) return -1;
    }
    double wait = nd_sum_value(&s->scratch);
    /* ProductionJob's math.fsum also rejects finite-term overflow. */
    if (!isfinite(wait)) return -2;
    double late = now - due;
    if (nd_sum_add(&s->flow, now - arrival) || nd_sum_add(&s->late, late) ||
        nd_sum_add(&s->tardiness, late > 0 ? late : 0) || nd_sum_add(&s->wait, wait)) return -1;
    ++s->completed;
    if (late > 0) ++s->tardy;
    return 0;
}
static int mean(const nd_signal *s, double end, double length, double *out) {
    /* Copy before adding the open tail. Polling cannot split intervals or
     * perturb the persistent expansion, even when allocation fails. */
    if (s->area.n == SIZE_MAX || s->area.n + 1 > SIZE_MAX / sizeof(double)) return -1;
    nd_sum copy = {0};
    copy.capacity = s->area.n + 1;
    copy.p = calloc(copy.capacity, sizeof(double));
    if (!copy.p) return -1;
    if (s->area.n) memcpy(copy.p, s->area.p, s->area.n * sizeof(double));
    copy.n = s->area.n; copy.special = s->area.special;
    double term = (double)s->value * (end - s->t);
    int rc = nd_sum_add(&copy, term);
    if (!rc) *out = nd_sum_value(&copy) / length;
    nd_sum_destroy(&copy);
    return rc;
}
int nd_stock_snapshot(const nd_stock_state *s, double now, uint32_t servers, int horizon, nd_kpi_snapshot *out) {
    memset(out, 0, sizeof(*out));
    out->start = s->warmup; out->end = fmax(now, s->warmup);
    out->closed = now >= s->warmup && !horizon;
    if (s->completed) {
        double n = (double)s->completed;
        out->present = 31;
        out->values[0] = nd_sum_value(&s->flow) / n;
        out->values[1] = nd_sum_value(&s->late) / n;
        out->values[2] = nd_sum_value(&s->tardiness) / n;
        out->values[3] = (double)s->tardy / n;
        out->values[4] = nd_sum_value(&s->wait) / n;
    }
    double length = out->end - out->start;
    if (length > 0) {
        out->present |= 224;
        out->values[5] = (double)s->completed / length;
        if (mean(&s->busy, out->end, length, &out->values[6]) ||
            mean(&s->floor, out->end, length, &out->values[7])) return -1;
        out->values[6] /= (double)servers;
    }
    return 0;
}
