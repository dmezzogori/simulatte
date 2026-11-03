#ifndef ND_RETENTION_MODULE_H
#define ND_RETENTION_MODULE_H
#include <Python.h>
#include "retention.h"
int nd_add_retention_methods(PyObject *);
PyObject *nd_retention_stats_dict(const nd_storage_stats *);
PyObject *nd_stock_snapshot_dict(const nd_kpi_snapshot *);
#endif
