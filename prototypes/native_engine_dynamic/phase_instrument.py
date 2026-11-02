"""Checked instrumentation of disposable copies; source repositories stay intact."""

import ast
import hashlib
from pathlib import Path
import shutil

from .execution_project import ROOT, REPO

# (qualified function, diagnostic name, retain coarse timeline span).
RUNTIME = {
    "execution/snapshot.py": [
        ("freeze_runtime", "runtime.freeze", True),
        ("capture_source", "source.capture", True),
        ("capture_inputs", "inputs.capture", True),
        ("materialize", "source.materialize", True),
        ("_check_supervisor", "runtime.supervisor_check", True),
    ],
    "execution/runtime.py": [
        ("describe_runtime", "runtime.distributions", True),
        ("verify_runtime", "runtime.verify", True),
    ],
    "execution/imports.py": [("ImportPolicy.audit", "imports.audit", True)],
    "environment.py": [
        ("Environment.__init__", "environment.create", True),
        ("Environment.activate", "activation.total", True),
        ("Environment.fingerprint", "fingerprint.query", True),
    ],
    "trace/writer.py": [
        ("TraceRecorder.__init__", "trace.open", True),
        ("TraceRecorder.close", "trace.close", True),
    ],
    "execution/bootstrap.py": [("load_configuration", "model.import", True)],
    "execution/child.py": [("serve", "child.serve", True)],
    "execution/driver.py": [
        ("execute", "child.execute", True),
        ("execute.resolve", "model.resolve", True),
        ("execute.ready", "ready.barrier", True),
        ("_artifacts", "artifacts.transfer", True),
        ("metadata_segment", "metadata.transfer", True),
    ],
    "execution/coordinator.py": [
        ("Coordinator.__init__", "coordinator.open", True),
        ("Coordinator.prepare", "coordinator.prepare", True),
        ("Coordinator.start_experiment", "experiment.start", True),
        ("Coordinator.wait_experiment", "experiment.wait", True),
        ("Coordinator.close", "coordinator.close", True),
        ("SlotActor._run_job", "assignment.total", True),
        ("SlotActor._start_worker", "supervisor.start", True),
    ],
    "execution/publication.py": [
        ("publish", "result.publish", True),
        ("publish.assemble", "result.assemble", True),
    ],
    "stopping.py": [("execute_until", "managed.execute", True)],
    "execution/backend.py": [
        ("BackendBinding.prepare", "binding.prepare", True),
        ("BackendBinding.activate", "binding.activate", True),
        ("BackendBinding.finish", "binding.finish", True),
        ("BackendBinding.close", "binding.close", True),
    ],
}
PROJECT = {
    "execution_model.py": [("build", "model.build", True), ("verify_assets", "native.assets_verify", True)],
    "execution_adapter.py": [
        ("templates", "inputs.generate", True),
        ("ExecutionAdapter.prepare", "backend.prepare", True),
        ("ExecutionAdapter.advance", "backend.advance", False),
        ("ExecutionAdapter.state_digest", "backend.state_digest", True),
        ("ExecutionAdapter.finish", "backend.finish", True),
        ("ExecutionAdapter.close", "backend.close", True),
    ],
    "compiled.py": [("CompiledStream.state_bytes", "native.state_bytes", True),
                    ("extension", "native.extension_load", True)],
}


class Instrument(ast.NodeTransformer):
    def __init__(self, targets):
        self.targets = {name: (label, span) for name, label, span in targets}
        self.stack, self.found = [], set()

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()
        return node

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        qualified = ".".join(self.stack)
        self.generic_visit(node)
        self.stack.pop()
        if qualified in self.targets:
            label, span = self.targets[qualified]
            node.decorator_list.append(ast.Call(
                func=ast.Name(id="_phase_timed", ctx=ast.Load()),
                args=[ast.Constant(label), ast.Constant(span)], keywords=[],
            ))
            assert qualified not in self.found
            self.found.add(qualified)
        return node


def transform(text, targets, *, stopping=False, bootstrap=False):
    tree = ast.parse(text)
    transformer = Instrument(targets)
    tree = transformer.visit(tree)
    assert transformer.found == set(transformer.targets), (transformer.found, transformer.targets)
    index = 1 if isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) else 0
    while (index < len(tree.body) and isinstance(tree.body[index], ast.ImportFrom)
           and tree.body[index].module == "__future__"):
        index += 1
    helper = "from simulatte._phase_probe import timed as _phase_timed, call as _phase_call"
    if bootstrap:
        # This file is also executed under -I -S before package roots exist.
        # Its package import (used by driver/inspection) gets the real probe.
        helper = ("if __name__ == '__main__':\n"
                  "    def _phase_timed(*args):\n"
                  "        return lambda function: function\n"
                  "else:\n    " + helper)
    tree.body[index:index] = ast.parse(helper).body
    result = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    if stopping:
        assert result.count("            env.step()") == 1
        result = result.replace("            env.step()", "            _phase_call('managed.python_step', env.step)")
    if bootstrap:
        marker = "        from simulatte.execution.child import serve"
        assert result.count(marker) == 1
        result = result.replace(marker, "\n".join((
            "        from simulatte._phase_probe import configure as _phase_configure, finish as _phase_finish",
            "        _phase_configure(config.get('_phase_profile'))", marker,
        )))
        marker = "        return serve(config, connection)"
        assert result.count(marker) == 1
        result = result.replace(marker, "\n".join((
            "        try:", "            return serve(config, connection)",
            "        finally:", "            _phase_finish()",
        )))
    compile(result, "<instrumented>", "exec")
    return result


def instrument(runtime, project):
    runtime = Path(runtime)
    shutil.copytree(REPO / "src/simulatte", runtime / "simulatte",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(ROOT / "phase_probe.py", runtime / "simulatte/_phase_probe.py")
    changed = {}
    for root, prefix, mapping in ((runtime / "simulatte", "runtime", RUNTIME),
                                  (project / "prototypes/native_engine_dynamic", "project", PROJECT)):
        for name, targets in mapping.items():
            path = root / name
            original = path.read_text()
            measured = transform(original, targets, stopping=name == "stopping.py",
                                 bootstrap=name == "execution/bootstrap.py")
            path.write_text(measured)
            changed[prefix + "/" + name] = dict(
                original_sha256=hashlib.sha256(original.encode()).hexdigest(),
                instrumented_sha256=hashlib.sha256(measured.encode()).hexdigest(),
                targets=targets,
            )
    return changed
