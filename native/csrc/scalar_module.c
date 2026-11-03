#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "dynamic_engine.h"
#include <math.h>
#include <string.h>

#ifdef SCALAR_TEST_ALLOCATIONS
static size_t allocation_attempt, allocation_failure;
static void *test_malloc(size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return PyMem_Malloc(size);
}
static void *test_calloc(size_t n, size_t size) {
    if (++allocation_attempt == allocation_failure) return NULL;
    return PyMem_Calloc(n, size);
}
static PyObject *fail_allocation(PyObject *self, PyObject *value) {
    (void)self;
    size_t n = PyLong_AsSize_t(value);
    if (PyErr_Occurred()) return NULL;
    allocation_attempt = 0; allocation_failure = n;
    Py_RETURN_NONE;
}
#define PyMem_Malloc test_malloc
#define PyMem_Calloc test_calloc
#endif

#define CAPSULE "simulatte.research.scalar"
typedef struct {
    nd_engine *engine;
    uint32_t jobs, operations, servers;
    int mode, failed, sealed, success, busy;
    double last_arrival;
} scalar;

static scalar *get(PyObject *capsule, int accepting) {
    scalar *s = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!s) return NULL;
    if (!s->engine || s->failed || s->busy || (accepting && s->sealed)) {
        PyErr_SetString(PyExc_RuntimeError, "stream is closed, busy, failed or sealed");
        return NULL;
    }
    return s;
}
static PyObject *native_error(const char *message) {
    PyObject *kind = strstr(message, "allocation") ? PyExc_MemoryError :
        strstr(message, "record") && (strstr(message, "open") || strstr(message, "write") || strstr(message, "flush"))
        ? PyExc_OSError : PyExc_ValueError;
    PyErr_SetString(kind, message);
    return NULL;
}
static PyObject *status(scalar *s, int result) {
    if (result) { s->failed = 1; return native_error(nd_error(s->engine)); }
    Py_RETURN_NONE;
}
static int count(PyObject *o, uint32_t *out, int positive) {
    if (!PyLong_Check(o) || PyBool_Check(o)) {
        PyErr_SetString(PyExc_TypeError, "count must be an integer, not bool"); return -1;
    }
    unsigned long long value = PyLong_AsUnsignedLongLong(o);
    if (PyErr_Occurred()) return -1;
    if (value >= UINT32_MAX || (positive && !value)) {
        PyErr_SetString(PyExc_ValueError, "count outside native range"); return -1;
    }
    *out = (uint32_t)value;
    return 0;
}
static void destroy(PyObject *capsule) {
    scalar *s = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!s) { PyErr_Clear(); return; }
    nd_destroy(s->engine);
    PyMem_Free(s);
}
static PyObject *create(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *servers, *cap, *path;
    int policy, mode;
    uint32_t n, c;
    if (!PyArg_ParseTuple(args, "OOiiO", &servers, &cap, &policy, &mode, &path) ||
        count(servers, &n, 1) || count(cap, &c, 0)) return NULL;
    const char *p = NULL;
    if (path != Py_None) {
        if (!PyBytes_Check(path)) { PyErr_SetString(PyExc_TypeError, "path must be bytes or None"); return NULL; }
        p = PyBytes_AS_STRING(path);
        if (!PyBytes_GET_SIZE(path) || memchr(p, 0, (size_t)PyBytes_GET_SIZE(path))) {
            PyErr_SetString(PyExc_ValueError, "invalid trace path"); return NULL;
        }
    }
    scalar *s = PyMem_Calloc(1, sizeof(*s));
    if (!s) return PyErr_NoMemory();
    char error[256];
    Py_BEGIN_ALLOW_THREADS
    s->engine = nd_create(n, c, policy, mode, p, error, sizeof(error));
    Py_END_ALLOW_THREADS
    if (!s->engine) { PyMem_Free(s); return native_error(error); }
    s->servers = n; s->mode = mode;
    PyObject *capsule = PyCapsule_New(s, CAPSULE, destroy);
    if (!capsule) { nd_destroy(s->engine); PyMem_Free(s); }
    return capsule;
}
static PyObject *accepting(PyObject *self, PyObject *capsule) {
    (void)self;
    if (!get(capsule, 1)) return NULL;
    Py_RETURN_NONE;
}
static PyObject *now(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 0);
    return s ? PyFloat_FromDouble(nd_now(s->engine)) : NULL;
}
static PyObject *add(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *routes, *durations;
    double arrival, due;
    if (!PyArg_ParseTuple(args, "OddOO", &capsule, &arrival, &due, &routes, &durations)) return NULL;
    scalar *s = get(capsule, 1);
    if (!s) return NULL;
    if (!PyTuple_CheckExact(routes) || !PyTuple_CheckExact(durations)) {
        PyErr_SetString(PyExc_TypeError, "route and durations must be exact tuples"); return NULL;
    }
    Py_ssize_t length = PyTuple_GET_SIZE(routes);
    if (!length || (uint64_t)length >= UINT32_MAX - s->operations || s->jobs >= UINT32_MAX - 1 ||
        PyTuple_GET_SIZE(durations) != length || !isfinite(arrival) || !isfinite(due) ||
        arrival < s->last_arrival || arrival < nd_now(s->engine) || (size_t)length > SIZE_MAX / sizeof(double)) {
        PyErr_SetString(PyExc_ValueError, "invalid arrival, operation count or duration lengths"); return NULL;
    }
    uint32_t *r = PyMem_Malloc((size_t)length * sizeof(*r));
    double *d = PyMem_Malloc((size_t)length * sizeof(*d));
    if (!r || !d) { PyMem_Free(r); PyMem_Free(d); return PyErr_NoMemory(); }
    for (Py_ssize_t i = 0; i < length; ++i) {
        if (count(PyTuple_GET_ITEM(routes, i), &r[i], 0)) goto invalid;
        PyObject *value = PyTuple_GET_ITEM(durations, i);
        if ((!PyFloat_CheckExact(value) && !PyLong_CheckExact(value)) || PyBool_Check(value)) {
            PyErr_SetString(PyExc_TypeError, "duration must be a primitive number"); goto invalid;
        }
        d[i] = PyFloat_AsDouble(value);
        if (PyErr_Occurred()) goto invalid;
        if (r[i] >= s->servers || !isfinite(d[i]) || d[i] <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid route or duration"); goto invalid;
        }
        for (Py_ssize_t j = 0; j < i; ++j)
            if (r[i] == r[j]) { PyErr_SetString(PyExc_ValueError, "repeated route server"); goto invalid; }
    }
    uint32_t id;
    int rc;
    s->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = nd_add(s->engine, arrival, due, (uint32_t)length, r, (size_t)length, d, (size_t)length, &id);
    Py_END_ALLOW_THREADS
    s->busy = 0;
    PyMem_Free(r); PyMem_Free(d);
    if (rc) { s->failed = 1; return native_error(nd_error(s->engine)); }
    ++s->jobs; s->operations += (uint32_t)length; s->last_arrival = arrival;
    return PyLong_FromUnsignedLong(id);
invalid:
    PyMem_Free(r); PyMem_Free(d); return NULL;
}
static PyObject *advance(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    double until;
    if (!PyArg_ParseTuple(args, "Od", &capsule, &until)) return NULL;
    scalar *s = get(capsule, 1);
    if (!s) return NULL;
    if (!isfinite(until) || until < nd_now(s->engine)) {
        PyErr_SetString(PyExc_ValueError, "invalid advance time"); return NULL;
    }
    int rc; s->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = nd_advance(s->engine, until);
    Py_END_ALLOW_THREADS
    s->busy = 0;
    return status(s, rc);
}
static PyObject *run(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 1);
    if (!s) return NULL;
    int rc; s->busy = 1; s->sealed = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = nd_run(s->engine);
    Py_END_ALLOW_THREADS
    s->busy = 0; s->success = !rc;
    return status(s, rc);
}
static PyObject *flush(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 0);
    if (!s) return NULL;
    int rc; s->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    rc = nd_flush(s->engine);
    Py_END_ALLOW_THREADS
    s->busy = 0;
    return status(s, rc);
}
static PyObject *inspect(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *identity;
    uint32_t id;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &identity) || count(identity, &id, 0)) return NULL;
    scalar *s = get(capsule, 0);
    if (!s) return NULL;
    nd_snapshot snapshot;
    if (nd_inspect(s->engine, id, &snapshot)) {
        PyErr_SetString(PyExc_ValueError, "invalid job handle ID"); return NULL;
    }
    PyObject *released = snapshot.state >= 2 ? PyFloat_FromDouble(snapshot.released) : Py_NewRef(Py_None);
    PyObject *finished = snapshot.state == 3 ? PyFloat_FromDouble(snapshot.finished) : Py_NewRef(Py_None);
    if (!released || !finished) { Py_XDECREF(released); Py_XDECREF(finished); return NULL; }
    return Py_BuildValue("{s:I,s:d,s:N,s:N,s:s}", "id", id, "arrival", snapshot.arrival,
                         "released", released, "finished", finished, "state",
                         (const char *[]) {"scheduled", "pool", "active", "finished"}[snapshot.state]);
}
static PyObject *double_list(const double *values, uint32_t n) {
    PyObject *list = PyList_New(n);
    if (!list) return NULL;
    for (uint32_t i = 0; i < n; ++i) {
        PyObject *value = PyFloat_FromDouble(values[i]);
        if (!value) { Py_DECREF(list); return NULL; }
        PyList_SET_ITEM(list, i, value);
    }
    return list;
}
static PyObject *id_list(const uint32_t *values, uint32_t n) {
    PyObject *list = PyList_New(n);
    if (!list) return NULL;
    for (uint32_t i = 0; i < n; ++i) {
        PyObject *value = PyLong_FromUnsignedLong(values[i]);
        if (!value) { Py_DECREF(list); return NULL; }
        PyList_SET_ITEM(list, i, value);
    }
    return list;
}
static int put(PyObject *dict, const char *key, PyObject *value) {
    if (!value) return -1;
    int rc = PyDict_SetItemString(dict, key, value);
    Py_DECREF(value);
    return rc;
}
static PyObject *result(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 0);
    if (!s) return NULL;
    const nd_result *r = nd_get_result(s->engine);
    if (!s->success || !r) { PyErr_SetString(PyExc_RuntimeError, "result requires a successful run"); return NULL; }
    s->busy = 1;
    PyObject *out = PyDict_New(), *kpi = NULL;
    if (!out) goto failed;
#define PUT(dict, key, value) do { if (put(dict, key, value)) goto failed; } while (0)
#define DOUBLE(dict, key) PUT(dict, #key, PyFloat_FromDouble(r->key))
#define INTEGER(dict, key) PUT(dict, #key, PyLong_FromUnsignedLongLong(r->key))
    PUT(out, "starts", double_list(r->starts, s->operations));
    PUT(out, "ends", double_list(r->ends, s->operations));
    PUT(out, "finished", double_list(r->finished, s->jobs));
    PUT(out, "released", double_list(r->released, s->jobs));
    PUT(out, "dispatch", id_list(r->dispatch, s->operations));
    DOUBLE(out, makespan); INTEGER(out, completed); INTEGER(out, events); INTEGER(out, callbacks); INTEGER(out, records);
    if (s->mode) {
        kpi = PyDict_New();
        if (!kpi) goto failed;
        DOUBLE(kpi, total_flow); DOUBLE(kpi, total_wait); DOUBLE(kpi, total_tardiness);
        PUT(kpi, "busy", double_list(r->busy, s->servers));
        DOUBLE(kpi, wip_area); INTEGER(kpi, max_wip);
        DOUBLE(kpi, total_pool_wait); DOUBLE(kpi, floor_wip_area); INTEGER(kpi, max_floor_wip);
    } else kpi = Py_NewRef(Py_None);
    if (PyDict_SetItemString(out, "kpis", kpi)) goto failed;
    Py_DECREF(kpi); s->busy = 0;
    return out;
failed:
    Py_XDECREF(kpi); Py_XDECREF(out); s->busy = 0; return NULL;
#undef PUT
#undef DOUBLE
#undef INTEGER
}
static PyObject *enable_stock_kpis(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule;
    double warmup;
    if (!PyArg_ParseTuple(args, "Od", &capsule, &warmup)) return NULL;
    scalar *s = get(capsule, 1);
    if (!s) return NULL;
    return status(s, nd_enable_stock_kpis(s->engine, warmup));
}
static PyObject *stock_kpis(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 0);
    if (!s) return NULL;
    nd_kpi_snapshot snapshot;
    int rc = nd_stock_kpis(s->engine, &snapshot);
    if (rc == -2) return PyErr_NoMemory();
    if (rc) { PyErr_SetString(PyExc_RuntimeError, "stock KPIs unavailable"); return NULL; }
    const char *names[] = {"shopfloor/makespan", "shopfloor/lateness", "shopfloor/tardiness",
        "shopfloor/tardy_fraction", "shopfloor/total_queue_time", "shopfloor/throughput",
        "shopfloor/utilization", "shopfloor/jobs_in_system"};
    PyObject *values = PyDict_New();
    if (!values) return NULL;
    for (unsigned i = 0; i < 8; ++i) {
        if ((snapshot.present & (1u << i)) && put(values, names[i], PyFloat_FromDouble(snapshot.values[i]))) {
            Py_DECREF(values); return NULL;
        }
    }
    return Py_BuildValue("{s:(ddO),s:N}", "window", snapshot.start, snapshot.end,
                         snapshot.closed ? Py_True : Py_False, "scalars", values);
}
static PyObject *state_bytes(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = get(capsule, 0);
    if (!s) return NULL;
    size_t size;
    if (nd_state_size(s->engine, &size) || size > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_RuntimeError, "native state size unavailable"); return NULL;
    }
    s->busy = 1;
    PyObject *bytes = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)size);
    if (!bytes) { s->busy = 0; return NULL; }
    unsigned char *destination = (unsigned char *)PyBytes_AS_STRING(bytes);
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = nd_state_write(s->engine, destination, size);
    Py_END_ALLOW_THREADS
    s->busy = 0;
    if (rc) {
        Py_DECREF(bytes);
        if (rc == -2) return PyErr_NoMemory();
        PyErr_SetString(PyExc_RuntimeError, "native state unavailable"); return NULL;
    }
    return bytes;
}
static PyObject *close_stream(PyObject *self, PyObject *capsule) {
    (void)self;
    scalar *s = PyCapsule_GetPointer(capsule, CAPSULE);
    if (!s) return NULL;
    if (s->busy) { PyErr_SetString(PyExc_RuntimeError, "stream is busy"); return NULL; }
    nd_engine *e = s->engine;
    s->engine = NULL; s->busy = 1;
    Py_BEGIN_ALLOW_THREADS
    nd_destroy(e);
    Py_END_ALLOW_THREADS
    s->busy = 0;
    Py_RETURN_NONE;
}
static PyMethodDef methods[] = {
#ifdef SCALAR_TEST_ALLOCATIONS
    {"_fail_allocation", fail_allocation, METH_O, NULL},
#endif
    {"create", create, METH_VARARGS, NULL}, {"accepting", accepting, METH_O, NULL},
    {"add", add, METH_VARARGS, NULL}, {"advance", advance, METH_VARARGS, NULL},
    {"run", run, METH_O, NULL}, {"inspect", inspect, METH_VARARGS, NULL},
    {"flush", flush, METH_O, NULL},
    {"enable_stock_kpis", enable_stock_kpis, METH_VARARGS, NULL},
    {"stock_kpis", stock_kpis, METH_O, NULL},
    {"state_bytes", state_bytes, METH_O, NULL},
    {"now", now, METH_O, NULL}, {"result", result, METH_O, NULL},
    {"close", close_stream, METH_O, NULL}, {NULL, NULL, 0, NULL}
};
static struct PyModuleDef definition = {
    PyModuleDef_HEAD_INIT, "_nd_scalar", "Research scalar boundary, GIL-enabled interpreters only.",
    0, methods, NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC PyInit__nd_scalar(void) { return PyModule_Create(&definition); }
