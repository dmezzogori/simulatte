"""Publish numerical runs, then execute both named analyses in the same headless API."""

from pathlib import Path
from simulatte.analysis import AnalysisStore, AnalysisRunner, Selection, codec
from simulatte.analysis.command import prepare_analysis
from simulatte.execution.coordinator import Coordinator
from simulatte.execution.snapshot import discover_project
from simulatte.execution.store import RunStore

root = Path(__file__).resolve().parent
for model_key, analysis_key in (("production", "lateness"), ("logistics", "utilization")):
    project = discover_project(root / f"models/{model_key}.py", root, layout=f"layouts/{model_key}.json")
    with Coordinator(project, reference=project.model + ":build") as coordinator:
        prepared = coordinator.prepare()
        metadata = next(m for m in prepared.metadata["models"] if m["name"] == prepared.metadata["selected"])
        plan = metadata["measurement_plans"][0]
        selected = {
            "schema": 1,
            "plan": plan["name"],
            "selection": codec.to_json({}),
            "definition_hash": codec.identity(plan),
        }
        request = coordinator.make_request(prepared, level="kpi", seed=17, measurements=selected)
        result = coordinator.wait(coordinator.submit(request, prepared.revision))
        attempt = result.key.attempt_id
    with RunStore.open(root / ".simulatte") as store:
        analysis = AnalysisStore(store)
        definition, generation = prepare_analysis(store, analysis_key)
        item = analysis.admit(
            analysis.preview(Selection(run_ids=(attempt,))).id,
            definition_id=definition,
            parameters={},
            generation_id=generation,
        )
        with AnalysisRunner(store) as runner:
            runner.submit(item.id)
            completed = runner.wait(item.id)
        if completed.state != "succeeded":
            raise RuntimeError(f"Analysis failed: {completed}")
        print(f"{analysis_key}: run={attempt} analysis={item.id} snapshot={item.snapshot_hash}")
