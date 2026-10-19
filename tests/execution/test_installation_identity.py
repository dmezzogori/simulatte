"""Installation-independent evidence retains actual payload and entrypoint code."""

import csv
import importlib
import json

import pytest

from simulatte.execution.runtime import describe_runtime


def api():
    return importlib.import_module("simulatte.execution.installation")


def installed(root, *, trampoline=False):
    scripts = root / "bin"
    scripts.mkdir(parents=True)
    site = root / "lib/python3.14/site-packages"
    site.mkdir(parents=True)
    info = site / "example-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: example\nVersion: 1.0\n")
    (site / "example.py").write_text("VALUE = 1\n")
    python = str(scripts / "python")
    artifact = root / "build/vendor/example.whl"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wheel content")
    body = b"import example\nprint(example.VALUE)\n"
    selector = (
        f"#!/bin/sh\n'''exec' '{python}' \"$0\" \"$@\"\n' '''\n" if trampoline else "#!" + python + "\n"
    ).encode()
    (scripts / "example").write_bytes(selector + body)
    (info / "direct_url.json").write_text(
        json.dumps({"url": (root / "build/vendor/example.whl").as_uri(), "archive_info": {}})
    )
    (info / "uv_cache.json").write_text(
        json.dumps(
            {
                "timestamp": {"secs_since_epoch": int(len(str(root))), "nanos_since_epoch": 12},
                "commit": None,
                "tags": None,
                "env": {},
                "directories": {},
            }
        )
    )
    rows = [["example.py", "sha256=original", "10"], ["../../../bin/example", "sha256=machine-specific", "100"]]
    rows += [[str(p.relative_to(site)), "sha256=metadata", str(p.stat().st_size)] for p in info.iterdir()]
    rows += [["example-1.0.dist-info/RECORD", "", ""]]
    with (info / "RECORD").open("w") as out:
        csv.writer(out).writerows(rows)
    return site, scripts, python


def probe(root, *, trampoline=False):
    site, scripts, python = installed(root, trampoline=trampoline)
    return (
        api().describe_installation(
            [str(site)],
            bindings={(root / "build/vendor/example.whl").as_uri(): "a" * 64},
            prefix=root,
            interpreter=python,
        ),
        site,
        scripts,
    )


def test_relocation_changes_raw_record_but_not_payload_identity(tmp_path):
    first, site, _ = probe(tmp_path / "original")
    second, other, _ = probe(tmp_path / "a longer copied project", trampoline=True)
    assert first == second
    # Raw legacy hashes retain their strict, separate meaning.
    assert describe_runtime([str(site)]) != describe_runtime([str(other)])


@pytest.mark.parametrize("target", ["payload", "entrypoint", "metadata", "artifact"])
def test_meaningful_changes_cannot_match_captured_identity(tmp_path, target):
    captured, site, scripts = probe(tmp_path / "environment")
    bindings = {(tmp_path / "environment/build/vendor/example.whl").as_uri(): "a" * 64}
    if target == "payload":
        (site / "example.py").write_text("VALUE = 2\n")
    elif target == "entrypoint":
        with (scripts / "example").open("ab") as out:
            out.write(b'print("changed body")\n')
    elif target == "metadata":
        (site / "example-1.0.dist-info/METADATA").write_text("Name: example\nVersion: 1.1\n")
    else:
        bindings[(tmp_path / "environment/build/vendor/example.whl").as_uri()] = "b" * 64
    actual = api().describe_installation(
        [str(site)], bindings=bindings, prefix=tmp_path / "environment", interpreter=str(scripts / "python")
    )
    assert actual != captured


def test_uncaptured_local_artifact_is_not_replaced_by_a_version_claim(tmp_path):
    site, scripts, python = installed(tmp_path / "environment")
    with pytest.raises(ValueError, match="captured"):
        api().describe_installation([str(site)], bindings={}, prefix=tmp_path / "environment", interpreter=python)


def test_exact_source_binding_checks_bytes_and_rejects_external_suffix(tmp_path):
    site, scripts, python = installed(tmp_path / "environment")
    root = tmp_path / "environment/build"
    files = {"vendor/example.whl": b"wheel content".hex()}
    bindings = api().bind_artifacts(files, root)
    assert bindings[(root / "vendor/example.whl").as_uri()] == api().digest(b"wheel content")
    info = site / "example-1.0.dist-info/direct_url.json"
    info.write_text(json.dumps({"url": (tmp_path / "outside/vendor/example.whl").as_uri(), "archive_info": {}}))
    with pytest.raises(ValueError, match="bound"):
        api().describe_installation([str(site)], bindings=bindings, prefix=tmp_path / "environment", interpreter=python)
    (root / "vendor/example.whl").write_bytes(b"changed wheel")
    with pytest.raises(ValueError, match="differs"):
        api().bind_artifacts(files, root)


@pytest.mark.parametrize(
    "change", ["duplicate", "escape", "absolute", "missing", "no-record", "malformed", "timestamp", "metadata-extra"]
)
def test_manifest_ambiguity_missing_payload_and_unknown_metadata_are_not_ignored(tmp_path, change):
    root = tmp_path / "env"
    captured, site, scripts = probe(root)
    info = site / "example-1.0.dist-info"
    record = info / "RECORD"
    bindings = {(root / "build/vendor/example.whl").as_uri(): "a" * 64}
    if change == "duplicate":
        with record.open("a") as out:
            out.write("example.py,,\n")
    elif change == "escape":
        with record.open("a") as out:
            out.write("../../../../../../outside,,\n")
    elif change == "absolute":
        with record.open("a") as out:
            out.write(str(site / "example.py") + ",,\n")
    elif change == "missing":
        (site / "example.py").unlink()
    elif change == "no-record":
        record.unlink()
    elif change == "malformed":
        record.write_text("bad,row\n")
    elif change == "timestamp":
        (info / "uv_cache.json").write_text('{"timestamp":{"unknown":true}}')
    else:
        (info / "uv_cache.json").write_text('{"timestamp":null,"extra":"must be retained"}')
    if change == "metadata-extra":
        assert (
            api().describe_installation(
                [str(site)], bindings=bindings, prefix=root, interpreter=str(scripts / "python")
            )
            != captured
        )
    else:
        with pytest.raises((ValueError, OSError)):
            api().describe_installation(
                [str(site)], bindings=bindings, prefix=root, interpreter=str(scripts / "python")
            )


@pytest.mark.parametrize(
    "script",
    [b"not a script", b"#!/bin/sh\nwrong\n' '''\nbody", b"#!/bin/sh\n'''exec' /one /two \"$0\" \"$@\"\n' '''\nbody"],
)
def test_unknown_script_selectors_refuse(script):
    with pytest.raises(ValueError):
        api().script_identity(script, "/one")


@pytest.mark.parametrize("url", ["file://other/path.whl", "file:///path.whl?x=1", "file:///path.whl#fragment"])
def test_ambiguous_local_urls_refuse(url):
    with pytest.raises(ValueError):
        api()._direct_url(json.dumps({"url": url}), {})


def test_remote_provenance_is_retained_and_only_declared_generated_bytecode_excluded(tmp_path):
    root = tmp_path / "env"
    _, site, scripts = probe(root)
    info = site / "example-1.0.dist-info"
    (info / "direct_url.json").write_text(
        '{"url":"https://example.org/package.whl","archive_info":{"hash":"unchanged"}}'
    )
    cache = site / "__pycache__/example.cpython-314.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"generated bytecode")
    with (info / "RECORD").open("a") as out:
        out.write("__pycache__/example.cpython-314.pyc,,\n")
    first = api().describe_installation([str(site)], bindings={}, prefix=root, interpreter=str(scripts / "python"))
    cache.unlink()  # Generated optional cache can disappear without changing installed code.
    assert first == api().describe_installation(
        [str(site)], bindings={}, prefix=root, interpreter=str(scripts / "python")
    )
    with (info / "RECORD").open("a") as out:
        out.write("alias.py,,\n")
    (site / "alias.py").symlink_to(site / "example.py")
    with pytest.raises(ValueError, match="Duplicate"):
        api().describe_installation([str(site)], bindings={}, prefix=root, interpreter=str(scripts / "python"))


@pytest.mark.parametrize("names", [[None], ["example", "Example"]])
def test_installed_names_are_unambiguous(monkeypatch, names):
    from types import SimpleNamespace

    distributions = [
        SimpleNamespace(metadata={"Name": name}, read_text=lambda key: "", locate_file=lambda path: ".", version="1")
        for name in names
    ]
    monkeypatch.setattr(api().metadata, "distributions", lambda **kw: distributions)
    with pytest.raises(ValueError, match="name|Ambiguous"):
        api().describe_installation([], bindings={})


def test_generated_windows_and_non_script_entrypoints_are_in_manifest(tmp_path):
    from tests.execution.test_installation_launchers import pe_launcher

    root = tmp_path / "env"
    _, site, scripts = probe(root)
    (site / "example-1.0.dist-info/direct_url.json").write_text('{"url":"https://example.org/artifact"}')
    python = r"C:\env\python.exe"
    (scripts / "example").write_bytes(pe_launcher(python))
    value = api().describe_installation([str(site)], bindings={}, prefix=root, interpreter=python)
    entry = value["dependencies"]["packages"]["example"]["files"]["@environment/bin/example"]
    assert entry["format"] == "uv-pe-resources-v1"
    (scripts / "example").write_bytes(b"non-script executable")
    value = api().describe_installation([str(site)], bindings={}, prefix=root, interpreter=python)
    assert value["dependencies"]["packages"]["example"]["files"]["@environment/bin/example"] == {
        "sha256": api().digest(b"non-script executable")
    }
