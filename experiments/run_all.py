"""Run every experiment reported in the STAR paper.

Seeds 100+ are evaluation seeds; seeds 0-7 were used during development only.
Usage:  python experiments/run_all.py [--quick]
Outputs: results/*.csv and results/*.jsonl
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trcn_star.scenarios import disaster, seven_node  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "results")
QUICK = "--quick" in sys.argv
N20 = 4 if QUICK else 20
N10 = 3 if QUICK else 10
SEED0 = 100

MAIN = ["Static", "ETX-DV", "Q-routing", "PRoPHET", "Epidemic", "Spray&Wait", "STAR"]
ABL = ["STAR", "STAR-noTS", "STAR-noRisk", "STAR-noStab", "STAR-noE2E", "STAR-noWatch",
       "STAR-noSpray", "STAR-noDecay"]


def _disaster_job(args):
    exp, router, seed, kw = args
    t = time.time()
    sim, out, _ = disaster(seed, router, return_sim=True, **kw)
    out["exp"] = exp
    out["wall"] = time.time() - t
    detail = None
    if exp in ("main", "aftershock"):
        t0, t1 = 900.0, 900.0 + kw.get("duration", 3600.0)
        msgs = [(m.created, m.priority, m.delivered_at, m.confirmed_at)
                for m in sim.messages.values() if m.kind != "receipt" and t0 <= m.created < t1]
        calib = []
        for psi, prio, uid in sim.stats.calib:
            m = sim.messages[uid]
            if m.kind != "receipt" and t0 <= m.created < t1:
                ok = m.delivered_at is not None and m.delivered_at <= m.expiry
                calib.append((psi, prio, int(ok)))
        detail = {"exp": exp, "router": router, "seed": seed, "msgs": msgs, "calib": calib}
    return out, detail


def _seven_job(args):
    router, seed, n = args
    _sim, s = seven_node(seed, router, n)
    s.update(exp="seven", n_submitted=n)
    return s, None


def jobs():
    J = []
    for r in MAIN:
        for s in range(SEED0, SEED0 + N20):
            J.append((_disaster_job, ("main", r, s, {})))
            J.append((_disaster_job, ("aftershock", r, s, {"aftershock": True})))
    for r in ABL[1:]:
        for s in range(SEED0, SEED0 + N20):
            J.append((_disaster_job, ("abl_main", r, s, {})))
            J.append((_disaster_job, ("abl_after", r, s, {"aftershock": True})))
    for rate in (1, 2, 4, 8, 12, 16):
        for r in MAIN:
            for s in range(SEED0, SEED0 + N10):
                J.append((_disaster_job, ("load", r, s, {"rate": float(rate)})))
    for k in (0, 1, 2, 3):
        for r in MAIN + ["STAR-noWatch", "STAR-noE2E"]:
            for s in range(SEED0, SEED0 + N10):
                J.append((_disaster_job, ("blackhole", r, s, {"blackholes": k})))
    for r in ("Static", "ETX-DV", "STAR"):
        for n in (1, 5, 10, 20, 40, 80):
            for s in range(SEED0, SEED0 + N10):
                J.append((_seven_job, (r, s, n)))
    return J


def _run(job):
    fn, args = job
    return fn(args)


def main():
    os.makedirs(OUT, exist_ok=True)
    J = jobs()
    # longest jobs first for better load balance
    J.sort(key=lambda j: 0 if "STAR" in str(j[1][1 if j[0] is _disaster_job else 0]) else 1)
    t = time.time()
    rows, details = [], []
    with Pool(os.cpu_count()) as pool:
        for i, (row, det) in enumerate(pool.imap_unordered(_run, J, chunksize=1)):
            rows.append(row)
            if det:
                details.append(det)
            if (i + 1) % 100 == 0:
                print(f"{i + 1}/{len(J)} jobs, {time.time() - t:.0f}s", flush=True)
    keys = sorted({k for r in rows for k in r})
    with open(os.path.join(OUT, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with open(os.path.join(OUT, "details.jsonl"), "w") as f:
        for d in details:
            f.write(json.dumps(d) + "\n")
    print(f"done: {len(rows)} runs in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
