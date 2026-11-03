#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "retention_module.h"
#include <math.h>
#include <string.h>
#define CAPSULE "simulatte.native.retention-v1"
typedef struct { bs_engine *engine; uint32_t servers; int busy, failed, sealed; } owner;
static owner *get(PyObject *capsule, int accepting) {
    owner *o = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!o) return NULL;
    if (!o->engine || o->busy || o->failed || (accepting && o->sealed)) {
        PyErr_SetString(PyExc_RuntimeError, "stream is closed, busy, failed or sealed"); return NULL;
    }
    return o;
}
static PyObject *error(owner *o) {
    o->failed = 1;
    const char *text = bs_error(o->engine);
    PyErr_SetString(strstr(text, "allocation") ? PyExc_MemoryError : PyExc_ValueError, text);
    return NULL;
}
static int integer(PyObject *p, uint32_t *n, int positive) {
    if (!PyLong_CheckExact(p)) { PyErr_SetString(PyExc_TypeError, "primitive integer required"); return -1; }
    unsigned long long value = PyLong_AsUnsignedLongLong(p);
    if (PyErr_Occurred()) return -1;
    if (value >= UINT32_MAX || (positive && !value)) {
        PyErr_SetString(PyExc_ValueError, "count outside uint32 range"); return -1;
    }
    *n = (uint32_t)value; return 0;
}
static void destroy(PyObject *capsule) {
    owner *o = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!o) { PyErr_Clear(); return; }
    bs_destroy(o->engine); PyMem_Free(o);
}
static PyObject *create(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *servers, *cap; uint32_t n, c; int policy, bounded; double warmup;
    if (!PyArg_ParseTuple(args, "OOiid", &servers, &cap, &policy, &bounded, &warmup) ||
        integer(servers, &n, 1) || integer(cap, &c, 0)) return NULL;
    if ((bounded != 0 && bounded != 1) || !isfinite(warmup) || warmup < 0) {
        PyErr_SetString(PyExc_ValueError, "invalid retention or warmup"); return NULL;
    }
    owner *o = PyMem_Calloc(1, sizeof(*o));
    if (!o) return PyErr_NoMemory();
    char message[256];
    o->engine = bs_create(n, c, policy, bounded, warmup, message, sizeof(message));
    if (!o->engine) { PyMem_Free(o); PyErr_SetString(PyExc_MemoryError, message); return NULL; }
    o->servers = n;
    PyObject *capsule = PyCapsule_New(o, CAPSULE, destroy);
    if (!capsule) { bs_destroy(o->engine); PyMem_Free(o); }
    return capsule;
}
static PyObject *add(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *routes, *durations; double arrival, due;
    if (!PyArg_ParseTuple(args, "OddOO", &capsule, &arrival, &due, &routes, &durations)) return NULL;
    owner *o = get(capsule, 1); if (!o) return NULL;
    if (!PyTuple_CheckExact(routes) || !PyTuple_CheckExact(durations)) {
        PyErr_SetString(PyExc_TypeError, "exact input tuples required"); return NULL;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(routes);
    if (n <= 0 || (uint64_t)n >= UINT32_MAX || n != PyTuple_GET_SIZE(durations) ||
        !isfinite(arrival) || !isfinite(due) || arrival < bs_now(o->engine) ||
        (size_t)n > SIZE_MAX / sizeof(double)) {
        PyErr_SetString(PyExc_ValueError, "invalid job input"); return NULL;
    }
    uint32_t *r = PyMem_Malloc((size_t)n * sizeof(*r));
    double *d = PyMem_Malloc((size_t)n * sizeof(*d));
    if (!r || !d) { PyMem_Free(r); PyMem_Free(d); return PyErr_NoMemory(); }
    for (Py_ssize_t i = 0; i < n; ++i) {
        if (integer(PyTuple_GET_ITEM(routes, i), r + i, 0)) goto invalid;
        PyObject *v = PyTuple_GET_ITEM(durations, i);
        if (!PyFloat_CheckExact(v) && !PyLong_CheckExact(v)) {
            PyErr_SetString(PyExc_TypeError, "primitive duration required"); goto invalid;
        }
        d[i] = PyFloat_AsDouble(v);
        if (PyErr_Occurred()) goto invalid;
        if (r[i] >= o->servers || !isfinite(d[i]) || d[i] <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid operation"); goto invalid;
        }
        for (Py_ssize_t j = 0; j < i; ++j) if (r[i] == r[j]) {
            PyErr_SetString(PyExc_ValueError, "repeated server"); goto invalid;
        }
    }
    uint64_t id; int rc;
    o->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = bs_add(o->engine, arrival, due, (uint32_t)n, r, d, &id);
    Py_END_ALLOW_THREADS
    o->busy = 0; PyMem_Free(r); PyMem_Free(d);
    if (rc) return error(o);
    PyObject *value = PyLong_FromUnsignedLongLong(id);
    if (!value) o->failed = 1; /* Admission happened; never return incomplete provenance. */
    return value;
invalid:
    PyMem_Free(r); PyMem_Free(d); return NULL;
}
static PyObject *step(PyObject *args, int seal) {
    PyObject *capsule; double until = 0;
    if (seal) capsule = args;
    else if (!PyArg_ParseTuple(args, "Od", &capsule, &until)) return NULL;
    owner *o = get(capsule, 1); if (!o) return NULL;
    if (!seal && (!isfinite(until) || until < bs_now(o->engine))) {
        PyErr_SetString(PyExc_ValueError, "invalid advance time"); return NULL;
    }
    int rc; o->busy = 1; o->sealed = seal;
    Py_BEGIN_ALLOW_THREADS
    rc = seal ? bs_run(o->engine) : bs_advance(o->engine, until);
    Py_END_ALLOW_THREADS
    o->busy = 0;
    if (rc) return error(o);
    const unsigned char *p; size_t n;
    if (bs_completions(o->engine, &p, &n)) return error(o);
    if (n > PY_SSIZE_T_MAX) { o->failed = 1; return PyErr_NoMemory(); }
    PyObject *value = PyBytes_FromStringAndSize((const char *)p, (Py_ssize_t)n);
    if (!value) o->failed = 1;
    return value;
}
static PyObject *advance(PyObject *self, PyObject *args) { (void)self; return step(args, 0); }
static PyObject *run(PyObject *self, PyObject *capsule) { (void)self; return step(capsule, 1); }
static PyObject *inspect(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *identity;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &identity)) return NULL;
    owner *o = get(capsule, 0); if (!o) return NULL;
    if (!PyLong_CheckExact(identity)) { PyErr_SetString(PyExc_TypeError, "integer identity required"); return NULL; }
    uint64_t id = PyLong_AsUnsignedLongLong(identity);
    if (PyErr_Occurred()) return NULL;
    nd_snapshot s; int rc = bs_inspect(o->engine, id, &s);
    if (rc) { PyErr_SetString(rc == -3 ? PyExc_KeyError : PyExc_ValueError, "expired or invalid job handle"); return NULL; }
    PyObject *released = s.state >= 2 ? PyFloat_FromDouble(s.released) : Py_NewRef(Py_None);
    PyObject *finished = s.state == 3 ? PyFloat_FromDouble(s.finished) : Py_NewRef(Py_None);
    if (!released || !finished) { Py_XDECREF(released); Py_XDECREF(finished); return NULL; }
    return Py_BuildValue("{s:K,s:d,s:N,s:N,s:s}", "id", (unsigned long long)id, "arrival", s.arrival,
        "released", released, "finished", finished, "state",
        (const char *[]) {"scheduled", "pool", "active", "finished"}[s.state]);
}
static PyObject *snapshot(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = get(capsule, 0); if (!o) return NULL;
    nd_kpi_snapshot s; int rc = bs_kpis(o->engine, &s);
    if (rc == -2) return PyErr_NoMemory();
    if (rc) { PyErr_SetString(PyExc_RuntimeError, "KPI snapshot unavailable"); return NULL; }
    PyObject *values = PyTuple_New(8); if (!values) return NULL;
    for (unsigned i = 0; i < 8; ++i) {
        PyObject *v = s.present & (1u << i) ? PyFloat_FromDouble(s.values[i]) : Py_NewRef(Py_None);
        if (!v) { Py_DECREF(values); return NULL; } PyTuple_SET_ITEM(values, i, v);
    }
    return Py_BuildValue("(ddON)", s.start, s.end, s.closed ? Py_True : Py_False, values);
}
static PyObject *state(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = get(capsule, 0); if (!o) return NULL;
    const unsigned char *p; size_t n; int rc = bs_state(o->engine, &p, &n);
    if (rc == -2) return PyErr_NoMemory();
    if (rc || n > PY_SSIZE_T_MAX) { PyErr_SetString(PyExc_RuntimeError, "state unavailable"); return NULL; }
    return PyBytes_FromStringAndSize((const char *)p, (Py_ssize_t)n);
}
PyObject *nd_stock_snapshot_dict(const nd_kpi_snapshot *s) {
    const char *names[] = {"shopfloor/makespan", "shopfloor/lateness", "shopfloor/tardiness",
        "shopfloor/tardy_fraction", "shopfloor/total_queue_time", "shopfloor/throughput",
        "shopfloor/utilization", "shopfloor/jobs_in_system"};
    PyObject *values = PyDict_New(); if (!values) return NULL;
    for (unsigned i = 0; i < 8; ++i) {
        if (!(s->present & (1u << i))) continue;
        PyObject *v = PyFloat_FromDouble(s->values[i]);
        if (!v) { Py_DECREF(values); return NULL; }
        int rc = PyDict_SetItemString(values, names[i], v); Py_DECREF(v);
        if (rc) { Py_DECREF(values); return NULL; }
    }
    return Py_BuildValue("{s:(ddO),s:N}", "window", s->start, s->end,
                         s->closed ? Py_True : Py_False, "scalars", values);
}
static PyObject *state_projection(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = get(capsule, 0); if (!o) return NULL;
    const unsigned char *p; size_t n; nd_kpi_snapshot stock; int rc;
    PyObject *raw = NULL, *snapshot = NULL, *result = NULL;
    o->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = bs_state(o->engine, &p, &n);
    if (!rc) rc = bs_kpis(o->engine, &stock);
    Py_END_ALLOW_THREADS
    if (rc || n > (size_t)PY_SSIZE_T_MAX) {
        if (rc == -2) PyErr_NoMemory();
        else PyErr_SetString(PyExc_RuntimeError, "native projection unavailable");
        goto done;
    }
    raw = PyBytes_FromStringAndSize((const char *)p, (Py_ssize_t)n);
    if (!raw) goto done;
    snapshot = nd_stock_snapshot_dict(&stock);
    if (snapshot) result = Py_BuildValue("{s:s,s:s,s:O,s:O}",
        "schema", "native-provider-raw-projection-v1", "codec", "NBST0001",
        "native", raw, "snapshot", snapshot);
done:
    Py_XDECREF(raw); Py_XDECREF(snapshot); o->busy = 0;
    return result;
}
PyObject *nd_retention_stats_dict(const nd_storage_stats *s) {
    return Py_BuildValue("{s:K,s:K,s:K,s:K,s:n,s:n,s:n,s:n,s:n,s:n,s:n,s:n}",
        "admitted", (unsigned long long)s->admitted, "completed", (unsigned long long)s->completed,
        "forgotten", (unsigned long long)s->forgotten, "events", (unsigned long long)s->events,
        "retained_jobs", (Py_ssize_t)s->jobs, "retained_operations", (Py_ssize_t)s->operations,
        "job_capacity", (Py_ssize_t)s->job_capacity, "op_capacity", (Py_ssize_t)s->op_capacity,
        "calendar_capacity", (Py_ssize_t)s->calendar_capacity, "native_bytes", (Py_ssize_t)s->native_bytes,
        "completion_capacity", (Py_ssize_t)s->completion_capacity, "state_capacity", (Py_ssize_t)s->state_capacity);
}
static PyObject *stats(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = get(capsule, 0); if (!o) return NULL;
    bs_stats s;
    if (bs_statistics(o->engine, &s)) { PyErr_SetString(PyExc_RuntimeError, "statistics unavailable"); return NULL; }
    return nd_retention_stats_dict(&s);
}
static PyObject *accepting(PyObject *self, PyObject *capsule) {
    (void)self; if (!get(capsule, 1)) return NULL; Py_RETURN_NONE;
}
static PyObject *now(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = get(capsule, 0); return o ? PyFloat_FromDouble(bs_now(o->engine)) : NULL;
}
static PyObject *close_stream(PyObject *self, PyObject *capsule) {
    (void)self; owner *o = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!o) return NULL;
    if (o->busy) { PyErr_SetString(PyExc_RuntimeError, "stream is busy"); return NULL; }
    bs_destroy(o->engine); o->engine = NULL; Py_RETURN_NONE;
}
static PyMethodDef methods[] = {
    {"retention_create", create, METH_VARARGS, NULL}, {"retention_add", add, METH_VARARGS, NULL},
    {"retention_advance", advance, METH_VARARGS, NULL}, {"retention_run", run, METH_O, NULL},
    {"retention_inspect", inspect, METH_VARARGS, NULL}, {"retention_snapshot", snapshot, METH_O, NULL},
    {"retention_state", state, METH_O, NULL}, {"retention_stats", stats, METH_O, NULL},
    {"retention_state_projection", state_projection, METH_O, NULL},
    {"retention_now", now, METH_O, NULL}, {"retention_close", close_stream, METH_O, NULL},
    {"retention_accepting", accepting, METH_O, NULL}, {NULL, NULL, 0, NULL}
};
int nd_add_retention_methods(PyObject *module) { return PyModule_AddFunctions(module, methods); }
