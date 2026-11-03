#include "../csrc/streaming_engine.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    char error[256]; nd_test_fail_allocation(0);
    bs_engine *b = bs_create(1, 0, 0, 1, 0, error, sizeof(error)); assert(b);
    bs_test_identity(b, (uint64_t)UINT32_MAX - 1);
    uint32_t route[] = {0}; double duration[] = {0.25};
    for (unsigned i = 0; i < 4; ++i) {
        uint64_t id; nd_snapshot snapshot;
        assert(!bs_add(b, i, i + 2, 1, route, duration, &id));
        assert(id == (uint64_t)UINT32_MAX - 1 + i);
        assert(!bs_inspect(b, id, &snapshot));
        assert(!bs_advance(b, i + 1));
        assert(bs_inspect(b, id, &snapshot) == -3);
    }
    bs_destroy(b);
    puts("Stable logical IDs cross uint32 boundary without aliasing");
    return 0;
}
