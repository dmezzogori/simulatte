"""Audit a completed longer Studio run and summarize runtime memory separately from storage."""

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import traceback

from analyze import HOSTS, digest, document, records, soak

MIB = 1 << 20
CASES = {"flow", "mixed", "burst"}
RETENTION = {"full", "bounded"}
GROWTH_LIMIT = {"chromium_heap": 8 * MIB, "server_rss": 32 * MIB, "server_fds": 8}


def runtime(root, host, summary, seconds):
    start = summary["started_at"]
    width = min(600, seconds // 4)
    windows = [
        dict(start_s=offset, end_s=min(offset + width, seconds), heap=[], rss=[], fds=[], store=[], children=[])
        for offset in range(0, seconds, width)
    ]

    def window(timestamp):
        elapsed = (timestamp - start) / 1000
        return windows[int(elapsed // width)] if 0 <= elapsed < seconds else None

    actions = {name: Counter() for name in ("reload-end", "reconnect", "seek-follow")}
    # Most rows are transport events already reconciled by soak(); skip decoding them again.
    with (root / f"{host}-browser/browser.jsonl").open() as stream:
        for line in stream:
            if '"type":"sample"' not in line and not any(f'"type":"{name}"' in line for name in actions):
                continue
            row = json.loads(line)
            if row["type"] in actions:
                actions[row["type"]][row["engine"]] += 1
            elif row["type"] == "sample" and row["heap"] is not None:
                if (entry := window(row["at"])) is not None:
                    entry["heap"].append(row["heap"])
    resource_path = root / f"{host}-server/resources.jsonl"
    for row in records(resource_path):
        if (entry := window(row["at"] * 1000)) is not None:
            entry["rss"].append(row["rss"])
            entry["fds"].append(row["fds"])
            entry["store"].append(row["store_bytes"])
            entry["children"].append(sum(child["rss"] for child in row["children"]))
    report = []
    for entry in windows:
        duration = entry["end_s"] - entry["start_s"]
        report.append(
            dict(
                start_s=entry["start_s"],
                end_s=entry["end_s"],
                heap_samples=len(entry["heap"]),
                server_samples=len(entry["rss"]),
                complete=len(entry["heap"]) >= duration * 0.6 / 15 and len(entry["rss"]) >= duration * 0.8 / 5,
                chromium_heap=median(entry["heap"]) if entry["heap"] else None,
                server_rss=median(entry["rss"]) if entry["rss"] else None,
                server_fds=median(entry["fds"]) if entry["fds"] else None,
                children_rss=median(entry["children"]) if entry["children"] else None,
                store_bytes=entry["store"][-1] if entry["store"] else None,
            )
        )
    # Use the second window as the reference. Two consecutive elevated windows
    # at the end flag sustained growth without treating a transient peak as a leak.
    reference, penultimate, final = report[1], report[-2], report[-1]
    review = {}
    for key, limit in GROWTH_LIMIT.items():
        values = [row[key] for row in (reference, penultimate, final)]
        if all(value is not None for value in values):
            early, earlier, late = values
            review[key] = dict(delta=late - early, limit=limit, flagged=min(earlier, late) - early >= limit)
    return dict(
        complete=all(row["complete"] for row in report),
        windows=report,
        growth_review=review,
        actions=actions,
        resources_sha256=digest(resource_path),
    )


def workloads(path):
    cases, configs, inconsistent = Counter(), {}, set()
    audit = document(path)
    for row in audit["rows"]:
        if row["status"] != "succeeded":
            continue
        params = row["parameters"]
        cases[params["case"], params["retention"]] += 1
        key = json.dumps([row["seed"], params], sort_keys=True)
        value = row["control"], row["kpis"]
        if configs.setdefault(key, value) != value:
            inconsistent.add(key)
    return (
        dict(
            all_cases_and_modes=set(cases) == {(case, mode) for case in CASES for mode in RETENTION},
            successes={f"{case}/{mode}": count for (case, mode), count in sorted(cases.items())},
            repeated_config_mismatches=sorted(inconsistent),
        ),
        configs,
    )


def main(args):
    root = args.evidence
    hosts, signatures = {}, {}
    for host in HOSTS:
        try:
            browser, server = (root / f"{host}-{kind}" for kind in ("browser", "server"))
            summary = document(browser / "summary.json")
            transport = soak(host, browser, server, evidence=root, minimum_seconds=args.seconds)
            memory = runtime(root, host, summary, args.seconds)
            models, signatures[host] = workloads(root / f"final-{host}-audit.json")
            assets = document(server / "server.json")["assets_sha256"]
            installed = document(root / f"{host}-environment.json")
            installed = {k: installed[k] for k in ("python", "platform", "installed", "wheels", "workload", "native")}
            checks = dict(
                charts_and_transport=transport["passed"],
                coverage=models["all_cases_and_modes"] and not models["repeated_config_mismatches"],
                continuous_chromium=summary["kept_open"] == ["chromium"]
                and transport["documents"].get("chromium") == 1
                and memory["actions"]["reload-end"]["chromium"] == 0,
                reloads=all(memory["actions"]["reload-end"][engine] > 0 for engine in ("firefox", "webkit")),
                samples=memory["complete"],
                no_sustained_growth_flag=set(memory["growth_review"]) == set(GROWTH_LIMIT)
                and not any(row["flagged"] for row in memory["growth_review"].values()),
            )
            hosts[host] = dict(
                passed=all(checks.values()),
                checks=checks,
                installed=installed,
                assets_sha256=assets,
                models=models,
                runtime=memory,
                transport=transport,
            )
        except Exception as error:
            traceback.print_exc()
            hosts[host] = dict(passed=False, error=f"{type(error).__name__}: {error}")
    mac, linux = (signatures.get(host, {}) for host in HOSTS)
    matching = mac.keys() & linux.keys()
    mismatches = sorted(key for key in matching if mac[key] != linux[key])
    cross_host = dict(shared_configs=len(matching), mismatches=mismatches)
    passed = all(row["passed"] for row in hosts.values()) and bool(matching) and not mismatches
    value = dict(passed=passed, duration_s=args.seconds, hosts=hosts, cross_host=cross_host)
    (root / "report.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(dict(passed=passed, hosts={host: row["passed"] for host, row in hosts.items()})))
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=7200)
    args = parser.parse_args()
    if args.seconds < 900:
        parser.error("Require at least 900 seconds of observations")
    raise SystemExit(0 if main(args) else 1)
