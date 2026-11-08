# TRCN — STAR learned routing + papers

This repo builds on the GPT research notes (`TRCN_v3.2_Protocol_Spec.md`, `Threat_Model.md`,
`Benchmark_Analysis.md`, `Roadmap_and_Costs.md`) and adds:

| Path | What it is |
|---|---|
| `trcn_star/` | Discrete-event TRCN simulator and the **STAR** routing model (+ 6 baselines) |
| `trcn_star/routing/star.py` | STAR: predicts route stability Ψ, decides next hop, learns from outcomes |
| `experiments/run_all.py` | Runs all 1,520 evaluation runs (seeds 100–119) → `results/` |
| `experiments/plots.py` | Regenerates every figure/table in `paper/star/` from `results/` |
| `experiments/recalibrate.py` | Held-out Platt recalibration of Ψ |
| `tests/` | 16 unit tests (PHY, links, posteriors, custody invariants, STAR behaviour) |
| `paper/star/main.pdf` | **Paper 1** — STAR: stability-aware Bayesian next-hop selection (14 pp.) |
| `paper/architecture/main.pdf` | **Paper 2** — TRCN architecture, protocol design and threat model (8 pp.) |
| `paper/references.bib` | Shared APA 7 bibliography (biblatex-apa) |

## The STAR model in one paragraph

Each node keeps discounted Beta posteriors over each link's frame success and availability
(updated from fragment-bitmap custody ACKs and beacons), plus loop-free (Babel feasibility),
sequence-authenticated distance-vector summaries `(μ, σ², π)` from neighbours. For every
neighbour j it predicts **Ψ = π̂ · Φ((T − μ)/σ)** — the probability the message arrives before
its deadline T. SOS/receipts use risk-averse lower bounds (and SOS is replicated over up to 3
next hops if Ψ < 0.95); normal traffic explores with Thompson sampling; among near-best Ψ the
lowest-delay hop wins; messages are held while the best link is believed down. It learns from
outcomes: per-round ACKs, signed end-to-end receipts treated as deadlines implied by the
neighbour's own claim, and watchdog overhearing; together these set a per-neighbour *trust =
calibration of its promises*, which defeats route poisoning.

## Reproduce

```bash
python3.13 -m venv .venv && .venv/bin/pip install numpy scipy matplotlib pytest networkx
.venv/bin/python -m pytest -q tests
.venv/bin/python experiments/run_all.py        # ~8 min on 8 cores (--quick for a smoke run)
.venv/bin/python experiments/plots.py
.venv/bin/python experiments/recalibrate.py
cd paper/star && latexmk -xelatex main.tex     # needs TeX Live/TinyTeX with apa7, biblatex-apa, biber, xeCJK
```

Papers use the `apa7` class in `jou` mode; change to `man` for an APA manuscript submission.
Author: Pinyen Cheng (Taoyuan, Taiwan). Both papers include a "Use of Generative AI" section.
Repository: https://github.com/chengmatt416/trcn

**All results are synthetic simulations**, not RF measurements.
