"""Figures and LaTeX tables for the STAR paper from results/summary.csv and details.jsonl."""
from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
RES = os.path.join(ROOT, "results")
FIG = os.path.join(ROOT, "paper", "star", "figures")
TAB = os.path.join(ROOT, "paper", "star", "tables")

# Fixed categorical order (validated reference palette, light mode); colour follows the router.
ORDER = ["STAR", "ETX-DV", "PRoPHET", "Static", "Epidemic", "Q-routing", "Spray&Wait"]
COLOR = dict(zip(ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]))
MARK = dict(zip(ORDER, ["o", "s", "^", "D", "v", "P", "X"]))
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.family": "serif", "font.size": 8.5, "axes.titlesize": 9, "axes.labelsize": 8.5,
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "legend.frameon": False, "lines.linewidth": 2,
    "lines.markersize": 5, "savefig.bbox": "tight", "pdf.fonttype": 42,
})


def load():
    rows = []
    with open(os.path.join(RES, "summary.csv")) as f:
        for r in csv.DictReader(f):
            for k, v in r.items():
                try:
                    r[k] = float(v)
                except (TypeError, ValueError):
                    pass
            rows.append(r)
    det = [json.loads(l) for l in open(os.path.join(RES, "details.jsonl"))]
    return rows, det


def ci(xs):
    xs = [x for x in xs if not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return float("nan"), float("nan")
    m = float(np.mean(xs))
    if len(xs) < 2:
        return m, 0.0
    from scipy import stats
    h = stats.t.ppf(0.975, len(xs) - 1) * np.std(xs, ddof=1) / math.sqrt(len(xs))
    return m, float(h)


def sel(rows, **kw):
    return [r for r in rows if all(r.get(k) == v for k, v in kw.items())]


def group(rows, key="router"):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return g


# ------------------------------------------------------------------ tables
METRICS = [("pdr", "PDR", 1), ("confirmed", "Confirmed", 1), ("sos_pdr", "SOS PDR", 1),
           ("lat_p50", "Median latency (s)", 0), ("lat_p95", "P95 latency (s)", 0),
           ("total_air_per_deliv", "Airtime / delivery (s)", 1)]


def fmt(m, h, pct):
    if math.isnan(m):
        return "--"
    if pct:
        return f"{100 * m:.1f} $\\pm$ {100 * h:.1f}"
    return f"{m:.0f} $\\pm$ {h:.0f}"


def fmt_air(m, h):
    return f"{m:.1f} $\\pm$ {h:.1f}"


def results_table(rows, exp, fname, caption, label, order=ORDER):
    g = group(sel(rows, exp=exp))
    lines = [r"\begin{table*}[t]", r"\centering", f"\\caption{{{caption}}}", f"\\label{{{label}}}",
             r"\small", r"\resizebox{\textwidth}{!}{%", r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"Router & PDR (\%) & Confirmed (\%) & SOS PDR (\%) & Median lat.\ (s) & P95 lat.\ (s) & Airtime/deliv.\ (s) \\",
             r"\midrule"]
    best = {}
    for k, _n, pct in METRICS:
        vals = {r: ci([x[k] for x in g[r]])[0] for r in order if r in g}
        if k in ("pdr", "confirmed", "sos_pdr"):
            best[k] = max(vals, key=vals.get)
        else:
            best[k] = min(vals, key=vals.get)
    for r in order:
        if r not in g:
            continue
        cells = []
        for k, _n, pct in METRICS:
            m, h = ci([x[k] for x in g[r]])
            s = fmt_air(m, h) if k == "total_air_per_deliv" else fmt(m, h, pct)
            if best[k] == r:
                s = r"\textbf{" + s + "}"
            cells.append(s)
        name = r"\textbf{STAR}" if r == "STAR" else r.replace("&", r"\&")
        lines.append(name + " & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}", r"\end{table*}"]
    open(os.path.join(TAB, fname), "w").write("\n".join(lines) + "\n")


def ablation_table(rows):
    names = ["STAR", "STAR-noTS", "STAR-noRisk", "STAR-noStab", "STAR-noE2E", "STAR-noWatch",
             "STAR-noSpray", "STAR-noDecay"]
    desc = {"STAR": "Full model", "STAR-noTS": "no Thompson sampling (posterior means)",
            "STAR-noRisk": "no risk-averse LCB for P0/P1", "STAR-noStab": r"no stability score ($\Psi$ $\to$ min.\ delay)",
            "STAR-noE2E": "no end-to-end learning or trust", "STAR-noWatch": "no watchdog overhearing",
            "STAR-noSpray": "no SOS replication", "STAR-noDecay": "no posterior discounting"}
    base = {"main": "main", "after": "aftershock"}
    lines = [r"\begin{table*}[t]", r"\centering",
             r"\caption{Ablation of STAR components (20 evaluation seeds, mean $\pm$ 95\% CI). PDR = packet delivery ratio.}",
             r"\label{tab:ablation}", r"\small", r"\resizebox{\textwidth}{!}{%", r"\begin{tabular}{llrrrrrr}", r"\toprule",
             r" & & \multicolumn{3}{c}{Baseline scenario} & \multicolumn{3}{c}{Aftershock scenario} \\",
             r"\cmidrule(lr){3-5}\cmidrule(lr){6-8}",
             r"Variant & Removed component & PDR (\%) & SOS (\%) & Med.\ lat.\ (s) & PDR (\%) & SOS (\%) & Med.\ lat.\ (s) \\",
             r"\midrule"]
    for n in names:
        cells = []
        for sc in ("main", "after"):
            exp = base[sc] if n == "STAR" else ("abl_main" if sc == "main" else "abl_after")
            rr = sel(rows, exp=exp, router=n)
            for k, pct in (("pdr", 1), ("sos_pdr", 1), ("lat_p50", 0)):
                m, h = ci([x[k] for x in rr])
                cells.append(fmt(m, h, pct))
        lines.append(n.replace("STAR-", "") if n != "STAR" else "STAR")
        lines[-1] = (r"\textbf{STAR}" if n == "STAR" else n) + " & " + desc[n] + " & " + " & ".join(cells) + r" \\"
    lines += [r"\bottomrule", r"\end{tabular}}", r"\end{table*}"]
    open(os.path.join(TAB, "ablation.tex"), "w").write("\n".join(lines) + "\n")


def seven_table(rows):
    g = sel(rows, exp="seven")
    v32 = {1: (1, 1, 84.058), 5: (5, 5, 97.858), 10: (10, 10, 114.858), 20: (20, 20, 149.708),
           40: (40, 40, 222.508), 80: (73, 71, 339.77)}
    lines = [r"\begin{table}[t]", r"\centering",
             r"\caption{Seven-node path replication. v3.2 = the original TRCN benchmark (single run, bit-rate airtime model); other columns are this simulator (LoRa time-on-air, 10 seeds, means). Deliveries counted within 600\,s of submission.}",
             r"\label{tab:seven}", r"\small", r"\resizebox{\linewidth}{!}{%", r"\begin{tabular}{rrrrrrr}", r"\toprule",
             r" & \multicolumn{2}{c}{v3.2 benchmark} & \multicolumn{2}{c}{Static (this work)} & \multicolumn{2}{c}{STAR} \\",
             r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
             r"Msgs & Deliv. & Delay (s) & Deliv. & Delay (s) & Deliv. & Delay (s) \\", r"\midrule"]
    for n in (1, 5, 10, 20, 40, 80):
        cells = [str(v32[n][0]), f"{v32[n][2]:.1f}"]
        for r in ("Static", "STAR"):
            rr = [x for x in g if x["router"] == r and x["n_submitted"] == n]
            cells.append(f"{np.mean([x['pdr'] for x in rr]) * n:.1f}")
            cells.append(f"{np.nanmean([x['lat_mean'] for x in rr]):.1f}")
        lines.append(f"{n} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}", r"\end{table}"]
    open(os.path.join(TAB, "seven.tex"), "w").write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ figures
def label_end(ax, x, y, r, dy=0.0):
    ax.annotate(r, (x, y), xytext=(4, dy), textcoords="offset points", fontsize=7, color=INK, va="center")


def fig_load(rows):
    g = sel(rows, exp="load")
    rates = sorted({r["rate"] for r in g})
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.4))
    for ax, (k, yl, pct) in zip(axes, [("pdr", "Delivery ratio (%)", True),
                                         ("lat_p50", "Median latency (s)", False),
                                         ("total_air_per_deliv", "Airtime per delivery (s)", False)]):
        for r in ORDER:
            ys, hs = [], []
            for rate in rates:
                m, h = ci([x[k] for x in g if x["router"] == r and x["rate"] == rate])
                ys.append(100 * m if pct else m)
                hs.append(100 * h if pct else h)
            ys, hs = np.array(ys), np.array(hs)
            ax.plot(rates, ys, color=COLOR[r], marker=MARK[r], label=r, lw=1.6)
            ax.fill_between(rates, ys - hs, ys + hs, color=COLOR[r], alpha=0.12, lw=0)
        ax.set_xlabel("Offered load (msgs / phone / h)")
        ax.set_ylabel(yl)
        ax.set_xticks(rates)
    axes[2].set_yscale("log")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=7, bbox_to_anchor=(0.5, 1.08), fontsize=7.5)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "load.pdf"))
    plt.close(fig)


def fig_aftershock(det):
    t0, t1, tA, binw = 900.0, 4500.0, 2700.0, 300.0
    bins = np.arange(t0, t1 + 1e-9, binw)
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    for r in ["STAR", "ETX-DV", "PRoPHET", "Static"]:
        num = np.zeros(len(bins) - 1)
        den = np.zeros(len(bins) - 1)
        for d in det:
            if d["exp"] != "aftershock" or d["router"] != r:
                continue
            for created, prio, dl, cf in d["msgs"]:
                i = int((created - t0) // binw)
                if 0 <= i < len(num):
                    den[i] += 1
                    num[i] += 1 if (dl is not None and dl - created <= 900.0) else 0
        y = 100 * num / np.maximum(den, 1)
        xc = (bins[:-1] + bins[1:]) / 2 - tA
        ax.plot(xc / 60, y, color=COLOR[r], marker=MARK[r], label=r, lw=1.6)
    ax.axvline(0, color=INK2, lw=1, ls="--")
    ax.text(0.5, 101, "aftershock", fontsize=7, color=INK2)
    ax.set_xlabel("Message creation time relative to aftershock (min)")
    ax.set_ylabel("Delivered within TTL (%)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "aftershock.pdf"))
    plt.close(fig)


def fig_blackhole(rows):
    g = sel(rows, exp="blackhole")
    ks = sorted({r["blackholes"] for r in g})
    fig, ax = plt.subplots(figsize=(3.4, 2.9))
    series = ORDER + ["STAR-noE2E"]
    for r in series:
        ys, hs = [], []
        for k in ks:
            m, h = ci([x["pdr"] for x in g if x["router"] == r and x["blackholes"] == k])
            ys.append(100 * m)
            hs.append(100 * h)
        if r == "STAR-noE2E":
            ax.plot(ks, ys, color=COLOR["STAR"], ls=":", marker="o", mfc="white", label="STAR w/o E2E+trust", lw=1.6)
        else:
            ax.errorbar(ks, ys, yerr=hs, color=COLOR[r], marker=MARK[r], label=r, lw=1.6, capsize=2)
    ax.set_xlabel("Compromised shelters (of 8)")
    ax.set_ylabel("Delivery ratio (%)")
    ax.set_xticks(ks)
    ax.set_ylim(0, 102)
    ax.legend(fontsize=6.5, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.30), handletextpad=0.3,
              columnspacing=0.8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "blackhole.pdf"))
    plt.close(fig)


def calib_data(det, exps=("main", "aftershock")):
    P, Y, PR = [], [], []
    for d in det:
        if d["router"] == "STAR" and d["exp"] in exps:
            for psi, prio, ok in d["calib"]:
                P.append(psi)
                Y.append(ok)
                PR.append(prio)
    return np.array(P), np.array(Y), np.array(PR)


def auc(p, y):
    from scipy.stats import rankdata
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    rk = rankdata(p)
    return float((rk[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def fig_calibration(det):
    P, Y, PR = calib_data(det)
    edges = np.array([0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0001])
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.5), gridspec_kw={"width_ratios": [1.2, 1]})
    for exps, r, lab, mk in ((("main",), "STAR", "baseline", "o"), (("aftershock",), "STAR", "aftershock", "s")):
        p, y, _ = calib_data(det, exps)
        xs, ys, ns = [], [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            s = (p >= lo) & (p < hi)
            if s.sum() >= 10:
                xs.append(p[s].mean())
                ys.append(y[s].mean())
                ns.append(s.sum())
        ax.plot(xs, ys, marker=mk, color=COLOR["STAR"] if lab == "baseline" else COLOR["ETX-DV"], label=f"STAR, {lab}")
    ax.plot([0, 1], [0, 1], color=INK2, lw=1, ls="--", label="perfect calibration")
    ax.set_xlabel(r"Predicted stability $\Psi$ at source")
    ax.set_ylabel("Observed delivery frequency")
    ax.set_xlim(0.3, 1.0)
    ax.set_ylim(0.3, 1.02)
    ax.legend(fontsize=7, loc="lower right")
    ax2.hist([P[Y == 1], P[Y == 0]], bins=20, range=(0, 1), stacked=True,
             color=[COLOR["STAR"], COLOR["ETX-DV"]], label=["delivered", "lost"], rwidth=0.9)
    ax2.set_yscale("log")
    ax2.set_xlabel(r"Predicted $\Psi$")
    ax2.set_ylabel("Messages (log)")
    ax2.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "calibration.pdf"))
    plt.close(fig)
    brier = float(np.mean((P - Y) ** 2))
    clim = float(np.mean((Y.mean() - Y) ** 2))
    return dict(n=len(P), brier=brier, climatology=clim, auc=auc(P, Y), base_rate=float(Y.mean()),
                mean_pred=float(P.mean()))


def fig_pareto(rows):
    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    for exp, fill in (("main", True), ("aftershock", False)):
        g = group(sel(rows, exp=exp))
        for r in ORDER:
            x, _ = ci([v["total_air_per_deliv"] for v in g[r]])
            y, _ = ci([v["pdr"] for v in g[r]])
            ax.scatter(x, 100 * y, s=40, marker=MARK[r], color=COLOR[r] if fill else "white",
                       edgecolor=COLOR[r], linewidth=1.5, label=r if fill else None, zorder=3)
    ax.set_xscale("log")
    from matplotlib.ticker import FixedLocator, NullLocator, FuncFormatter
    ax.xaxis.set_major_locator(FixedLocator([10, 20, 50, 100, 200]))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.set_xlabel("Total airtime per delivered message (s, log scale)")
    ax.set_ylabel("Delivery ratio (%)")
    ax.legend(fontsize=6.5, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.28), handletextpad=0.2,
              columnspacing=0.8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "pareto.pdf"))
    plt.close(fig)


def main():
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(TAB, exist_ok=True)
    rows, det = load()
    results_table(rows, "main", "main.tex",
                  r"Baseline disaster scenario (rate 4 msgs/phone/h; 20 evaluation seeds; mean $\pm$ 95\% CI). Best value per column in bold.",
                  "tab:main")
    results_table(rows, "aftershock", "aftershock.tex",
                  r"Aftershock scenario: at mid-run the microwave backbone and 30\% of shelter LoRa links fail permanently and one shelter crashes for 10\,min (20 seeds).",
                  "tab:aftershock")
    ablation_table(rows)
    seven_table(rows)
    fig_load(rows)
    fig_aftershock(det)
    fig_blackhole(rows)
    fig_pareto(rows)
    cal = fig_calibration(det)
    # headline numbers for the text
    stats = {"calibration": cal}
    for exp in ("main", "aftershock"):
        g = group(sel(rows, exp=exp))
        stats[exp] = {r: {k: ci([x[k] for x in g[r]]) for k in
                          ("pdr", "confirmed", "sos_pdr", "lat_p50", "lat_p95", "total_air_per_deliv",
                           "ctrl_air_per_deliv", "ctrl_airtime", "data_airtime")} for r in g}
    g = sel(rows, exp="blackhole")
    stats["blackhole"] = {f"{r}|{int(k)}": ci([x["pdr"] for x in g if x["router"] == r and x["blackholes"] == k])
                          for r in {x["router"] for x in g} for k in {x["blackholes"] for x in g}}
    g = sel(rows, exp="load")
    stats["load"] = {f"{r}|{int(k)}": {m: ci([x[m] for x in g if x["router"] == r and x["rate"] == k])
                                       for m in ("pdr", "lat_p50", "sos_pdr")}
                     for r in {x["router"] for x in g} for k in {x["rate"] for x in g}}
    json.dump(stats, open(os.path.join(RES, "headline.json"), "w"), indent=1)
    print(json.dumps(cal, indent=1))


if __name__ == "__main__":
    main()
