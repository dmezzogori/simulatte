"""User worker configuration: ``workers.toml`` parsing, strict validation and name resolution."""

import dataclasses
from pathlib import Path

import pytest

from simulatte.execution.workers import WorkerSpec, default_workers_path, load_workers, resolve_workers

CREDENTIALS = "credentials are not accepted in workers.toml; use ~/.ssh/config"


def write(tmp_path, text):
    path = tmp_path / "workers.toml"
    path.write_text(text, encoding="utf-8")
    return path


def rejects(tmp_path, text):
    path = write(tmp_path, text)
    with pytest.raises(ValueError) as caught:
        load_workers(path)
    return str(caught.value).replace(str(path), "<path>")


def test_parses_workers_with_defaults_and_explicit_fields(tmp_path):
    path = write(
        tmp_path,
        """
[workers.strangelove]
host = "me@strangelove"
slots = 4
ssh_args = ["-o", "ServerAliveInterval=15"]
uv = "~/.local/bin/uv"
cache = "/scratch/simulatte"
provision_timeout = 90.5

[workers.minimal]
host = "otacon"
""",
    )
    workers = load_workers(path)
    assert list(workers) == ["strangelove", "minimal"]
    assert workers["strangelove"] == WorkerSpec(
        name="strangelove",
        host="me@strangelove",
        slots=4,
        ssh_args=("-o", "ServerAliveInterval=15"),
        uv="~/.local/bin/uv",
        cache="/scratch/simulatte",
        provision_timeout=90.5,
    )
    assert workers["minimal"] == WorkerSpec(
        name="minimal",
        host="otacon",
        slots=1,
        ssh_args=(),
        uv=None,
        cache="~/.cache/simulatte",
        provision_timeout=600.0,
    )
    assert [field for field in WorkerSpec.__dataclass_fields__] == [
        "name",
        "host",
        "slots",
        "ssh_args",
        "uv",
        "cache",
        "provision_timeout",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(workers["minimal"], "slots", 2)


def test_empty_file_and_missing_file_mean_no_workers(tmp_path):
    assert load_workers(write(tmp_path, "")) == {}
    assert load_workers(tmp_path / "absent.toml") == {}


def test_environment_override_and_default_path(tmp_path, monkeypatch):
    path = write(tmp_path, '[workers.a]\nhost = "a"\n')
    monkeypatch.setenv("SIMULATTE_WORKERS", str(path))
    assert default_workers_path() == path
    assert list(load_workers()) == ["a"]
    monkeypatch.setenv("SIMULATTE_WORKERS", str(tmp_path / "nothing.toml"))
    assert load_workers() == {}
    monkeypatch.delenv("SIMULATTE_WORKERS")
    assert default_workers_path() == Path.home() / ".config" / "simulatte" / "workers.toml"
    monkeypatch.setenv("SIMULATTE_WORKERS", "")
    assert default_workers_path() == Path.home() / ".config" / "simulatte" / "workers.toml"


def test_malformed_and_unreadable_files_name_the_path(tmp_path):
    path = write(tmp_path, "[workers.a\nhost = ")
    with pytest.raises(ValueError, match="Malformed workers file") as caught:
        load_workers(path)
    assert str(path) in str(caught.value)
    binary = tmp_path / "binary.toml"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="Malformed workers file") as caught:
        load_workers(binary)
    assert str(binary) in str(caught.value)
    with pytest.raises(ValueError, match="Cannot read workers file") as caught:
        load_workers(tmp_path)
    assert str(tmp_path) in str(caught.value)


def test_structure_is_strict(tmp_path):
    assert rejects(tmp_path, 'extra = 1\n[workers.a]\nhost = "a"\n') == "<path>: unknown top-level key 'extra'"
    assert rejects(tmp_path, "workers = 3\n") == "<path>: 'workers' must be a table of worker tables"
    assert rejects(tmp_path, "[workers]\na = 3\n") == "<path>: workers.a: must be a table"
    assert rejects(tmp_path, '[workers.a]\nhost = "a"\nmystery = 1\n') == "<path>: workers.a: unknown key 'mystery'"


@pytest.mark.parametrize("name", ["local", "interactive", "local-2", "local-gpu"])
def test_reserved_names_are_rejected(tmp_path, name):
    message = rejects(tmp_path, f'[workers.{name}]\nhost = "h"\n')
    assert message == f"<path>: worker name '{name}' is reserved"


@pytest.mark.parametrize("name", ['"has space"', '"a/b"', '"é"', '"a:b"'])
def test_invalid_names_are_rejected(tmp_path, name):
    message = rejects(tmp_path, f'[workers.{name}]\nhost = "h"\n')
    assert message.startswith("<path>: invalid worker name ") and "letters, digits" in message


def test_localish_names_that_are_allowed(tmp_path):
    workers = load_workers(write(tmp_path, '[workers.localhost]\nhost = "h"\n[workers.a-local]\nhost = "h"\n'))
    assert list(workers) == ["localhost", "a-local"]


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "passphrase",
        "token",
        "secret",
        "key",
        "private_key",
        "identity",
        "identity_file",
        "api_key",
        "Password",
        "API_KEY",
        "ssh_password",
        "auth_token",
        "client_secret",
    ],
)
def test_credential_keys_are_rejected(tmp_path, key):
    message = rejects(tmp_path, f'[workers.a]\nhost = "h"\n{key} = "x"\n')
    assert message == f"<path>: workers.a.{key}: {CREDENTIALS}"


def test_credential_keys_are_rejected_at_top_level_too(tmp_path):
    assert rejects(tmp_path, 'password = "x"\n') == f"<path>: password: {CREDENTIALS}"


@pytest.mark.parametrize("host", ["user:pass@host", "me:s3cret@10.0.0.2", "a:@host"])
def test_password_style_hosts_are_rejected(tmp_path, host):
    assert rejects(tmp_path, f'[workers.a]\nhost = "{host}"\n') == f"<path>: workers.a.host: {CREDENTIALS}"


def test_host_validation(tmp_path):
    assert rejects(tmp_path, "[workers.a]\nslots = 1\n") == "<path>: workers.a: 'host' is required"
    for bad in ('""', "3", '"  "'):
        assert rejects(tmp_path, f"[workers.a]\nhost = {bad}\n") == "<path>: workers.a.host: must be a non-empty string"
    for bad in ('"-oProxyCommand=x"', '"a b"', '"a\\nb"'):
        assert rejects(tmp_path, f"[workers.a]\nhost = {bad}\n") == (
            "<path>: workers.a.host: must not start with '-' or contain whitespace"
        )
    assert list(load_workers(write(tmp_path, '[workers.a]\nhost = "me@[::1]"\n'))) == ["a"]


@pytest.mark.parametrize("value", ["0", "-1", "1.5", '"2"', "true"])
def test_slots_must_be_a_positive_integer(tmp_path, value):
    message = rejects(tmp_path, f'[workers.a]\nhost = "h"\nslots = {value}\n')
    assert message == "<path>: workers.a.slots: must be an integer >= 1"


@pytest.mark.parametrize("value", ['"-o"', '["-o", 3]', "3"])
def test_ssh_args_must_be_a_list_of_strings(tmp_path, value):
    message = rejects(tmp_path, f'[workers.a]\nhost = "h"\nssh_args = {value}\n')
    assert message == "<path>: workers.a.ssh_args: must be a list of strings"


@pytest.mark.parametrize("field", ["uv", "cache"])
@pytest.mark.parametrize("value", ['""', "3", "[]"])
def test_path_fields_must_be_non_empty_strings(tmp_path, field, value):
    message = rejects(tmp_path, f'[workers.a]\nhost = "h"\n{field} = {value}\n')
    assert message == f"<path>: workers.a.{field}: must be a non-empty string"


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf", '"60"', "true"])
def test_provision_timeout_must_be_a_positive_number(tmp_path, value):
    message = rejects(tmp_path, f'[workers.a]\nhost = "h"\nprovision_timeout = {value}\n')
    assert message == "<path>: workers.a.provision_timeout: must be a number > 0"


def test_resolve_workers_returns_specs_in_order_and_names_known_workers(tmp_path):
    workers = load_workers(write(tmp_path, '[workers.b]\nhost = "b"\n[workers.a]\nhost = "a"\n'))
    assert [spec.name for spec in resolve_workers(["b", "a"], workers)] == ["b", "a"]
    assert resolve_workers([], workers) == []
    with pytest.raises(ValueError) as caught:
        resolve_workers(["a", "x"], workers)
    assert str(caught.value) == "Unknown worker 'x'; known workers: a, b"
    with pytest.raises(ValueError) as caught:
        resolve_workers(["x"], {})
    assert str(caught.value) == "Unknown worker 'x'; known workers: none"
