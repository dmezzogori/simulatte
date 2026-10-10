"""Integrated public API acceptance, including native execution of published examples."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_layout_guide_examples_execute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    guide = (ROOT / "docs/guides/layout.md").read_text()
    blocks = re.findall(r"```python \{ \.run \}\n(.*?)\n```", guide, re.DOTALL)
    assert len(blocks) == 7, "Every layout use case must remain executable"
    for index, block in enumerate(blocks):
        exec(compile(block, f"layout.md:block-{index + 1}", "exec"), {"__name__": "__main__"})


def test_stage_file_edits_observers_and_saved_reuse(tmp_path):
    from benchmarks.layout_acceptance import fixture, model
    from simulatte.layout import reresolve_presentation

    file = tmp_path / "layout.json"
    baseline = fixture(file=file)
    recorded = fixture(file=file, record=tmp_path / "run.simtrace")
    assert baseline == recorded
    assert baseline["machine_ids"] == ["machine-00", "machine-01"]
    assert any(d["code"] == "orphan-placement" and d["target"] == "removed" for d in baseline["diagnostics"])
    narrower = fixture(width=3, file=file)
    assert narrower["machine_ids"] == ["machine-00"]
    assert any(d["code"] == "orphan-placement" and d["target"] == "machine-01" for d in narrower["diagnostics"])
    wider = fixture(width=9, file=file)
    assert wider["machine_ids"] == ["machine-00", "machine-01", "machine-02"]
    assert wider["hashes"]["physical_hash"] != baseline["hashes"]["physical_hash"]
    assert wider["trajectory"] == baseline["trajectory"]
    assert wider["fingerprint"]["kpis"] == baseline["fingerprint"]["kpis"]

    env, layout, _, _, _ = model(file=file)
    original = layout.prepare().bundle
    patch = layout.to_dict()
    patch["placements"]["machine-01"]["visual"]["color"] = "green"
    view = reresolve_presentation(original, patch)
    assert not view.requires_rerun
    assert view.bundle is not None
    assert view.bundle.physical_hash == original.physical_hash
    assert view.bundle.full_hash != original.full_hash
    assert original["resolved"]["placements"]["machine-01"]["visual"]["color"] == "blue"
    patch["placements"]["hall"]["footprint"]["width"] = 9
    changed = reresolve_presentation(original, patch)
    assert changed.requires_rerun and changed.bundle is None
    env.close()


def test_custom_binder_cannot_escape_closure_or_read_auto():
    import pytest
    from simulatte import Environment, Layout
    from simulatte.layout import Grid, Lattice, LayoutError, Point
    from simulatte.intralogistics.traffic import FreeTrafficManager
    from simulatte.server import Server

    class CustomTraffic(FreeTrafficManager):
        pass

    for declare_read, explicit in ((False, True), (True, False)):
        env = Environment()
        layout = Layout(env)
        layout.set_grid(Grid(1, 1))
        layout.set_network(Lattice((0, 0, 1, 0)))
        layout.register_resource_owner(CustomTraffic(), owner=object(), graph=layout.graph(), binder="custom")
        server = Server(env=env, capacity=1, name="server")
        if explicit:
            layout.place(server, position=Point(1, 2))
        position = layout.ref("placements", server.id, "position")
        layout.on_bind("custom", lambda ctx: ctx.physical(position), reads=[position] if declare_read else [])
        with pytest.raises(LayoutError, match="undeclared|auto"):
            layout.prepare()
        assert env.now == 0 and not env.activated
        env.close()


def test_generated_geometry_and_hashes_repeat_across_observers(tmp_path):
    from benchmarks.layout_acceptance import fixture

    for angle in (0, 90, 180, 270, 13.7):
        assert fixture(rotation=angle) == fixture(rotation=angle, record=tmp_path / f"{angle}.simtrace")
