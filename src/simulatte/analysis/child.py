"""One-use analysis entrypoint; no live store or latest-results query is available."""

from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys
from . import codec
from .context import AnalysisContext


def load(reference, source, roots):
    relative, separator, name = reference.partition(":")
    path = (source / relative).resolve()
    if not separator or not name.isidentifier() or not path.is_relative_to(source) or path.suffix != ".py":
        raise ValueError("Analysis entrypoint must name a function in captured Python source")
    module_name = "_simulatte_analysis"
    containing = [r for r in roots if path.is_relative_to(r)]
    if containing:
        parts = path.relative_to(max(containing, key=lambda r: len(r.parts))).with_suffix("").parts
        if len(parts) > 1 and all(p.isidentifier() for p in parts):
            module_name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load captured analysis")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    function = getattr(module, name)
    if not callable(function):
        raise ValueError("Analysis entrypoint is not callable")
    return function


def main(config_path):
    config = codec.loads(Path(config_path).read_bytes())
    source = Path(config["source"]).resolve()
    roots = tuple(source / r for r in config["python_path"])
    standard = tuple(Path(p).resolve() for p in sys.path if p and Path(p).exists())
    sys.path[:0] = [str(p) for p in roots]
    from simulatte.execution.imports import install_import_policy

    policy = install_import_policy(Path(config["project_root"]), roots, standard, source_root=source)
    os.chdir(source)
    function = load(config["entrypoint"], source, roots)
    if config.get("inspect"):
        from simulatte.analysis.definition import get_analysis_definition

        definition = get_analysis_definition(
            function, id=config["definition_id"], entrypoint=config["entrypoint"], source_hash=config["source_hash"]
        )
        from .context import write_data

        write_data(Path(config["output"]), definition.to_dict())
    else:
        context = AnalysisContext(
            config["inputs"],
            input_dir=Path(config["input_dir"]),
            output_dir=Path(config["output"]),
            requirements=config["requirements"],
        )
        function(context, config["parameters"])
        policy.audit()
        context.finish(config["execution_id"], config["provenance"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
