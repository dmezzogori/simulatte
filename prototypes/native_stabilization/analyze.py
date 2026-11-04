"""Validate and summarize the saved representative-model and timed-session evidence."""

from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def band(values):
    return dict(min=min(values), median=median(values), max=max(values), first=values[0], last=values[-1])


def main():
    root = ROOT / "results"
    trajectories = [read(root / f"{host}-trajectory.json") for host in ("macos", "linux")]
    assert trajectories[0] == trajectories[1] and trajectories[0]["cases"] == 36
    matrices = [read(root / f"{host}-matrix-final/matrix.json") for host in ("macos", "linux")]
    for value in matrices:
        assert value["passed"] and len(value["rows"]) == 168 and len(value["reproductions"]) == 12
    for a, b in zip(*(value["rows"] for value in matrices), strict=True):
        for name in ("parameters", "seed", "warmup", "control", "kpis", "samples"):
            assert a[name] == b[name]
    endurance = [read(root / f"{host}-endurance.json") for host in ("macos", "linux")]
    assert all(value["passed"] and len(value["rows"]) == 18 for value in endurance)
    for a, b in zip(*(value["rows"] for value in endurance), strict=True):
        for name in ("parameters", "control", "kpis"):
            assert a[name] == b[name]
    browser_resources = lines(root / "browser-resources.jsonl")
    hosts, cross_host = [], {}
    for host in ("macos", "linux"):
        browser_root = root / f"studio-browser-final-{host}"
        browser, logs = read(browser_root / "browser-summary.json"), lines(browser_root / "browser.jsonl")
        audit = read(root / f"{host}-audit.json")
        assert browser["seconds"] >= 900
        assert browser["passed"] == (not browser["errors"])
        settled = read(root / f"settled-{host}/settled.json")
        assert settled["passed"] and len(settled["rows"]) == 3
        assert all(row["view"]["tiles"] == row["view"]["charts"] == 8 for row in settled["rows"])
        assert read(root / f"{host}-revocation.json")["passed"]
        transport = read(root / f"{host}-transport.json")
        assert not transport["reproduced"] and transport["refreshed_source_matches"]
        assert browser["engines"] == ["chromium", "firefox", "webkit"]
        assert browser["reloads"] >= 8 and browser["reconnect_actions"] >= 20 and browser["seeks"] >= 6
        assert browser["results"] == 20
        assert audit["passed"] and len(audit["reproductions"]) == 6
        assert {"succeeded", "cancelled", "failed"} <= set(audit["statuses"])
        samples = [row for row in logs if row["type"] == "sample"]
        start, end = min(row["at"] for row in logs) / 1000, max(row["at"] for row in logs) / 1000
        resources = [row for row in lines(root / f"{host}-soak-final/resources.jsonl") if start <= row["at"] <= end]
        assert len(resources) >= 170
        heaps = [row["heap"]["after"] for row in samples if row["heap"]]
        late = resources[-len(resources) // 3 :]
        browser_stats = {}
        for engine in browser["engines"]:
            values = [row for row in samples if row["engine"] == engine]
            assert len(values) >= 50
            assert any("Native production · experimental" in row["view"]["info"] for row in values)
            assert 0 < max(row["view"]["tiles"] for row in values) <= 8
            browser_stats[engine] = dict(
                samples=len(values),
                complete_tile_samples=sum(row["view"]["tiles"] == 8 for row in values),
                nodes=band([row["view"]["nodes"] for row in values]),
                canvases=band([row["view"]["canvases"] for row in values]),
            )
            memory = [row for row in browser_resources if row["host"] == host and row["engine"] == engine]
            assert len(memory) >= 60
            browser_stats[engine]["late_process_tree_rss"] = band([row["rss"] for row in memory])
        control = {}
        for row in audit["rows"]:
            if row["status"] != "succeeded":
                continue
            key = json.dumps(dict(parameters=row["parameters"], seed=row["seed"]), sort_keys=True)
            physical = dict(control=row["control"], kpis=row["kpis"])
            if key in cross_host:
                assert physical == cross_host[key]
            cross_host[key] = physical
            control[row["parameters"]["case"], row["parameters"]["retention"]] = row
        hosts.append(
            dict(
                host=host,
                browser_error_gate_passed=browser["passed"],
                browser_errors=browser["errors"],
                settled=settled["rows"],
                duration=browser["seconds"],
                statuses=audit["statuses"],
                browser_actions=dict(
                    Counter(
                        row["type"] for row in logs if row["type"] in ("reload", "reconnect", "seek-follow", "cancel")
                    )
                ),
                successful_seeks=browser["seeks"],
                browser=browser_stats,
                chromium_collected_heap=band(heaps),
                chromium_heap_first10_median=median(heaps[:10]),
                chromium_heap_last10_median=median(heaps[-10:]),
                server_rss=band([row["rss"] for row in resources]),
                server_first_third_rss_median=median(row["rss"] for row in resources[: len(resources) // 3]),
                server_last_third_rss_median=median(row["rss"] for row in late),
                server_late_rss_range=max(row["rss"] for row in late) - min(row["rss"] for row in late),
                server_fds=band([row["fds"] for row in resources]),
                largest_child_rss=max(child["rss"] for row in resources for child in row["children"]),
                store_bytes=dict(first=resources[0]["store_bytes"], last=resources[-1]["store_bytes"]),
                retained_examples=[
                    dict(case=key[0], retention=key[1], control=row["control"], storage=row["storage"])
                    for key, row in sorted(control.items())
                ],
            )
        )
    value = dict(
        study_complete=True,
        passed=all(host["browser_error_gate_passed"] for host in hosts),
        modeling_gate_passed=True,
        publication_reproduction_gate_passed=True,
        studio_browser_gate_passed=all(host["browser_error_gate_passed"] for host in hosts),
        matrix_requests_per_host=168,
        matrix_reproductions_per_host=12,
        physical_trajectory_cases_per_host=36,
        physical_cross_host_equal=True,
        soak_reproductions_per_host=6,
        endurance_requests_per_host=18,
        product_changes=False,
        hosts=hosts,
        source={
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ROOT.glob("*"))
            if p.suffix in (".py", ".mjs", ".md")
        },
    )
    (root / "summary.json").write_text(json.dumps(value, indent=2) + "\n")
    certificate = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "verification.json"
    }
    (root / "verification.json").write_text(json.dumps(certificate, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                evidence_validated=True,
                studio_browser_gate_passed=value["studio_browser_gate_passed"],
                hosts=2,
                requests=372,
                trajectories=72,
                reproductions=36,
            )
        )
    )


if __name__ == "__main__":
    main()
