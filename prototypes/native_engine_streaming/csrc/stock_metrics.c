/* Keep the previously validated strict arithmetic in its own translation unit. */
#include "../../native_engine_dynamic/csrc/stock_kpi.c"
size_t bs_stock_bytes(const nd_stock_state *s) {
    if (!s) return 0;
    return sizeof(*s) + sizeof(double) * (s->flow.capacity + s->late.capacity +
        s->tardiness.capacity + s->wait.capacity + s->scratch.capacity +
        s->busy.area.capacity + s->floor.area.capacity);
}
