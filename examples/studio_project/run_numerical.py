"""Run both models in ordinary Python, keeping numerical rows without animation."""

from simulatte import Environment, Layout
from simulatte.model import get_model_spec
from models import production, logistics

for module in (production, logistics):
    spec = get_model_spec(module.build)
    with Environment(seed=17) as env:
        env.configure_kpis(warmup=spec.warmup)
        env.configure_measurements(module.PLAN)
        module.build(env, Layout(env), module.Params())
        env.run(until=spec.horizon)
    collector = next(c for c in env.collectors if hasattr(c, "rows"))
    if collector.failure:
        raise RuntimeError(collector.failure)
    print(f"{module.PLAN.name}: {len(collector.rows)} numerical rows")
