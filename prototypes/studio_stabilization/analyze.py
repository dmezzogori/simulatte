"""Correlate the installed experiments without treating harness events as body reads."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".build"
ENGINES = ("chromium", "firefox", "webkit")
HOSTS = ("macos", "linux")


def records(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def document(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def probes(host, phase, server):
    result = {}
    for engine, suffix in (("chromium", ""), ("webkit", "-webkit")):
        root = BUILD / f"{phase}-{host}-cancel{suffix}"
        browser = document(root / "summary.json")
        row = next(row for row in records(server / "reads.jsonl") if row["id"] == browser["server_id"])
        cancelled = next(
            item
            for item in records(root / "browser.jsonl")
            if item["type"] == "cancelled" and item["prefix"] == browser["prefix"]
        )
        assert cancelled["http"] == 410
        assert row["path"] == browser["prefix"] and row["status"] == browser["status"] == 206
        assert row["expected"] == browser["expected"] <= 1 << 20
        if phase == "baseline":
            assert row["error"] == "StaleAttempt" and not row["ended"] and row["sent"] == 0
            assert not browser["intact"] and browser["errors"]
        else:
            assert row["ended"] and row["error"] is None and row["sent"] == row["expected"]
            assert browser["intact"] and not browser["errors"]
        result[engine] = dict(browser=browser, server=row, revoked_http=410)
    return result


def chart_counts(samples):
    output = {}
    for engine in ENGINES:
        live = [
            row["view"]["charts"]
            for row in samples
            if row["engine"] == engine and "live committed prefix" in row["view"]["info"]
        ]
        assert live
        output[engine] = dict(
            live=len(live), eight=live.count(8), empty=live.count(0), partial=sum(0 < n < 8 for n in live)
        )
    return output


def short_trial(root):
    summary = document(root / "summary.json")
    assert summary["seconds"] >= 100
    samples = [row for row in records(root / "browser.jsonl") if row["type"] == "sample"]
    return dict(seconds=summary["seconds"], charts=chart_counts(samples), errors=summary["errors"])


def soak(host, root, server, *, evidence=None, minimum_seconds=900):
    summary = document(root / "summary.json")
    evidence = BUILD if evidence is None else evidence
    audit = document(evidence / f"final-{host}-audit.json")
    revocations = document(evidence / f"final-{host}-revocations.json")
    observed = summary["observed"]
    assert audit["passed"] and audit["statuses"] == dict(Counter(observed.values()))
    assert summary["seconds"] >= minimum_seconds and summary["engines"] == list(ENGINES)
    expected_outcomes = audit.get("expected_outcomes", set(observed.values()) == {"succeeded", "cancelled", "failed"})
    assert revocations["passed"]
    unsuccessful = {key for key, status in observed.items() if status != "succeeded"}
    assert {row["attempt"] for row in revocations["rows"] if row["http"] == 410} == unsuccessful
    assert summary["reloads"] >= 8 and summary["reconnects"] >= 10
    assert summary["results"] == 20
    samples, errors, failures = [], [], []
    starts, headers, outcomes = {}, {}, {}
    boundaries = defaultdict(list)
    reloads = defaultdict(list)
    scopes = defaultdict(set)
    actions = defaultdict(set)
    documents = {}
    document_ids = defaultdict(set)
    revocation_checks = {}
    for row in records(root / "browser.jsonl"):
        match row["type"]:
            case "sample":
                samples.append(row)
            case "error":
                errors.append(row)
            case "requestfailed":
                failures.append(row)
            case "read-start":
                if row["tag"]:
                    starts[row["tag"]] = row["path"]
                    documents[row["engine"]] = row["tag"].rsplit("-", 1)[0]
                    document_ids[row["engine"]].add(documents[row["engine"]])
                    scopes[row["engine"], row["path"]].add(row["scope"])
            case "read-headers":
                if row["tag"]:
                    headers[row["tag"]] = (row["status"], row["length"], row["method"])
                if row["status"] == 410 and row["path"].split("/")[3] in unsuccessful:
                    revocation_checks[row["server_id"]] = row
            case "fetch-outcome":
                outcomes[row["tag"]] = row
            case "reload-start" | "client-close":
                boundaries[row["engine"]].append([row["at"], row["at"] + 1000, documents.get(row["engine"])])
                if row["type"] == "reload-start" and row["key"] is not None:
                    reloads[row["engine"]].append((row["at"], row["key"]["attempt_id"]))
            case "reload-end":
                boundaries[row["engine"]][-1][1] = row["at"]
            case "seek-follow" | "reconnect":
                actions[row["type"]].add(row["engine"])
    assert actions["seek-follow"] == actions["reconnect"] == set(ENGINES)
    server_counts = Counter()
    invalid = []
    max_range = 0
    revoked = defaultdict(list)
    for row in records(server / "reads.jsonl"):
        server_counts[str(row["status"])] += 1
        if row["tag"] and row["status"] is not None:
            headers.setdefault(row["tag"], (row["status"], row["expected"], row["method"]))
        match = revocation_checks.get(row["id"])
        if match and row["status"] == 410 and row["path"] == match["path"] and row["client"] == match["engine"]:
            revoked[match["engine"], match["path"]].append(match["at"])
        if row["method"] == "GET" and row["status"] in (200, 206):
            max_range = max(max_range, row["expected"])
            if row["ended"] and row["sent"] == row["expected"] and row["error"] is None:
                server_counts["exact_body"] += 1
            elif row["disconnected"] and row["error"] is None:
                server_counts["client_disconnected"] += 1
            else:
                invalid.append(row)
    # Body outcomes are observed by the application's actual reader. A runner's
    # requestfailed event can disagree with an already-completed body.
    ok, expected = set(), set()
    unexpected = []
    read_counts = Counter()
    navigation = []
    exact_bytes = 0
    for tag, outcome in outcomes.items():
        path = starts.get(tag, "")
        status, size, method = headers.get(tag, (None, None, None))
        attempt = path.split("/")[3] if path else None
        if outcome["outcome"] == "complete" and status == 206 and outcome["bytes"] == size:
            ok.add(tag)
            read_counts["exact_body"] += 1
            exact_bytes += outcome["bytes"]
        elif outcome["outcome"] == "headers" and status == 200 and method == "HEAD":
            ok.add(tag)
            read_counts["head"] += 1
        elif outcome["outcome"] == "headers" and outcome["status"] == 410 and attempt in unsuccessful:
            expected.add(tag)
            read_counts["revoked"] += 1
            revoked[outcome["engine"], path].append(outcome["at"])
        elif outcome["outcome"].endswith("error") and outcome.get("aborted"):
            expected.add(tag)
            read_counts["signal_aborted"] += 1
        elif outcome["outcome"].endswith("error") and any(
            start <= outcome["at"] <= end and tag.rsplit("-", 1)[0] == page
            for start, end, page in boundaries[outcome["engine"]]
        ):
            expected.add(tag)
            read_counts["navigation_aborted"] += 1
            navigation.append(outcome)
        else:
            unexpected.append(dict(path=path, **outcome))

    runner = Counter()
    runner_unexplained = []
    for failure in failures:
        if failure["tag"] in ok:
            runner["body_or_head_completed"] += 1
        elif failure["tag"] in expected:
            runner["expected_abort_or_revocation"] += 1
        else:
            # A page can unload before the in-page instrumentation delivers an
            # outcome. Preserve these diagnostics separately from body failures.
            runner["no_observed_body_outcome"] += 1
            if failure["failure"] not in ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "cancelled"):
                runner_unexplained.append(failure)
    expected_console = [
        error
        for error in errors
        if error["kind"] == "console"
        and "410" in error["message"]
        and any(abs(at - error["at"]) < 1000 for at in revoked[error["engine"], error["path"]])
    ]
    # Playwright maps WebKit JavaScript-source console errors to pageerror. A
    # reload can revoke the old document's fetch access before pagehide. Require
    # the exact prefix, old read scope, reload and in-page rejection to agree.
    expected_navigation = []
    for error in errors:
        if error["engine"] != "webkit" or error["kind"] != "page":
            continue
        match = re.search(r"(/api/prefix/([0-9a-f]{32})/\d+) due to access control checks\.$", error["message"])
        if match is None:
            continue
        path, attempt = match.groups()
        if any(at <= error["at"] < at + 100 and key == attempt for at, key in reloads["webkit"]) and any(
            row["engine"] == "webkit" and abs(row["at"] - error["at"]) < 100 and row["scope"] in scopes["webkit", path]
            for row in navigation
        ):
            expected_navigation.append(error)
    unexpected_browser = [error for error in errors if error not in expected_console + expected_navigation]
    chart = chart_counts(samples)
    alerts = sorted({alert for row in samples for alert in row["view"]["alerts"]})
    heaps = [row["heap"] for row in samples if row["heap"] is not None]
    resource = [
        row for row in records(server / "resources.jsonl") if samples[0]["at"] <= row["at"] * 1000 <= samples[-1]["at"]
    ]
    assert len(resource) >= 170 and len(heaps) >= 50
    transport_ok = (
        not unexpected_browser
        and not alerts
        and not unexpected
        and not invalid
        and not runner_unexplained
        and all(value["eight"] > value["live"] * 0.75 and value["partial"] == 0 for value in chart.values())
    )
    return dict(
        passed=transport_ok and expected_outcomes,
        transport_ok=transport_ok,
        expected_outcomes=expected_outcomes,
        seconds=summary["seconds"],
        statuses=audit["statuses"],
        revoked_prefixes=len(revocations["rows"]),
        revocation_mode=revocations.get("mode", "live_http"),
        results=summary["results"],
        reloads=summary["reloads"],
        reconnects=summary["reconnects"],
        seeks=summary["seeks"],
        charts=chart,
        read_counts=read_counts,
        exact_range_bytes=exact_bytes,
        documents={engine: len(values) for engine, values in document_ids.items()},
        runner_diagnostics=runner,
        unexplained_runner_failures=runner_unexplained[:20],
        server_counts=server_counts,
        largest_body=max_range,
        browser_errors=errors,
        expected_revocation_console=expected_console,
        expected_reload_diagnostics=expected_navigation,
        unexpected_browser_errors=unexpected_browser,
        alerts=alerts,
        unexpected_read_outcomes=unexpected[:20],
        unexpected_read_count=len(unexpected),
        incomplete_server_responses=invalid[:20],
        incomplete_server_response_count=len(invalid),
        chromium_heap_bytes=dict(first10=median(heaps[:10]), last10=median(heaps[-10:]), peak=max(heaps)),
        server_rss_bytes=dict(
            first=resource[0]["rss"], last=resource[-1]["rss"], peak=max(row["rss"] for row in resource)
        ),
        store_bytes=dict(first=resource[0]["store_bytes"], last=resource[-1]["store_bytes"]),
        server_descriptors=dict(peak=max(row["fds"] for row in resource), last=resource[-1]["fds"]),
        browser_sha256=digest(root / "browser.jsonl"),
        server_reads_sha256=digest(server / "reads.jsonl"),
        publication_sha256=digest(evidence / f"final-{host}-audit.json"),
    )


def main():
    hosts = {}
    for host in HOSTS:
        baseline = BUILD / ("baseline-linux-instrumented" if host == "linux" else "baseline-macos-server")
        candidate = BUILD / f"candidate-{host}-server"
        installed = {}
        for phase in ("baseline", "final"):
            values = document(BUILD / f"{host}-{phase}.json")
            installed[phase] = {name: values[name] for name in ("core_sha256", "native_sha256")}
            installed[phase]["model_source"] = {key: values["source"][key] for key in ("plant.py", "workloads.py")}
        assert installed["baseline"]["native_sha256"] == installed["final"]["native_sha256"]
        assert installed["baseline"]["model_source"] == installed["final"]["model_source"]
        hosts[host] = dict(
            installed=installed,
            baseline_cancel=probes(host, "baseline", baseline),
            candidate_cancel=probes(host, "candidate", candidate),
            baseline_charts=short_trial(BUILD / f"baseline-{host}-charts"),
            candidate_charts=short_trial(BUILD / f"candidate-{host}-cancel-charts"),
            first_soak=first_soak(BUILD / f"soak-{host}"),
            soak=soak(host, BUILD / f"final-{host}", BUILD / f"final-{host}-server"),
        )
    assert hosts["macos"]["installed"]["final"]["core_sha256"] == hosts["linux"]["installed"]["final"]["core_sha256"]
    passed = all(host["soak"]["passed"] for host in hosts.values())
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    (results / "summary.json").write_text(json.dumps(dict(passed=passed, hosts=hosts), indent=2) + "\n")
    print(json.dumps(dict(passed=passed, statuses={key: host["soak"]["statuses"] for key, host in hosts.items()})))
    if not passed:
        raise SystemExit(1)


def first_soak(root):
    result = document(root / "summary.json")
    alerts = sorted(
        {alert for row in records(root / "browser.jsonl") if row["type"] == "sample" for alert in row["view"]["alerts"]}
    )
    assert result["seconds"] >= 900 and alerts
    return dict(passed=False, seconds=result["seconds"], alerts=alerts, browser_errors=result["errors"])


if __name__ == "__main__":
    main()
