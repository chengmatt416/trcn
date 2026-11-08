"""Held-out recalibration of STAR's stability score (Platt scaling).
Fit on evaluation seeds 100-109, test on 110-119; prints and saves results/recalibration.json."""
import json
import os

import numpy as np
from scipy.optimize import minimize

RES = os.path.join(os.path.dirname(__file__), "..", "results")
det = [json.loads(l) for l in open(os.path.join(RES, "details.jsonl"))]


def data(seeds):
    P, Y = [], []
    for d in det:
        if d["router"] == "STAR" and d["exp"] in ("main", "aftershock") and d["seed"] in seeds:
            for psi, _pr, ok in d["calib"]:
                P.append(psi)
                Y.append(ok)
    return np.clip(np.array(P), 1e-4, 1 - 1e-4), np.array(Y)


def logit(p):
    return np.log(p / (1 - p))


Ptr, Ytr = data(range(100, 110))
Pte, Yte = data(range(110, 120))


def nll(w):
    p = 1 / (1 + np.exp(-(w[0] * logit(Ptr) + w[1])))
    return -np.mean(Ytr * np.log(p + 1e-12) + (1 - Ytr) * np.log(1 - p + 1e-12))


w = minimize(nll, [1.0, 0.0]).x
pc = 1 / (1 + np.exp(-(w[0] * logit(Pte) + w[1])))
out = {"a": float(w[0]), "b": float(w[1]), "n_train": int(len(Ytr)), "n_test": int(len(Yte)),
       "brier_raw_test": float(np.mean((Pte - Yte) ** 2)),
       "brier_platt_test": float(np.mean((pc - Yte) ** 2)),
       "brier_climatology_test": float(np.mean((Ytr.mean() - Yte) ** 2)),
       "bins": []}
for lo, hi in [(0, .8), (.8, .9), (.9, .95), (.95, .98), (.98, 1.01)]:
    s = (pc >= lo) & (pc < hi)
    if s.sum():
        out["bins"].append([lo, hi, int(s.sum()), float(pc[s].mean()), float(Yte[s].mean())])
json.dump(out, open(os.path.join(RES, "recalibration.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
