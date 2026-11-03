"""Research adapter: dynamic-only input, no retained fixed-workload tuple."""

from simulatte.experimental.native import NativeProduction
from simulatte.execution.backend import BackendCapabilities
from .stream import Stream


class StreamingProduction(NativeProduction):
    capabilities = BackendCapabilities("native-streaming-research", "bounded-native-state-v1")

    def __init__(self, servers, *, input_state, retention="bounded", **kwargs):
        if input_state is None:
            raise TypeError("A compact future-input state callback is required")
        super().__init__(servers, input_state=input_state, **kwargs)
        self.retention = retention

    def prepare(self, context):
        if self._closed or self._stream is not None:
            raise RuntimeError("Research backend is single-use")
        self._context = context
        self._stream = Stream(
            self.servers,
            wip_cap=self.wip_cap,
            policy=self.policy,
            warmup=context.stopping.warmup,
            retention=self.retention,
            input_state=self._input_state,
        )

    def state_digest(self):
        if self._state is not None:
            return self._state
        self._synchronized()
        return self._live().state_digest()
