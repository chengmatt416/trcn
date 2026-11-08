"""Synthetic TRCN scenarios and router registry.

Every number in this file is a *synthetic assumption* chosen to create
heterogeneous, intermittently connected topologies; none is a measurement.
"""
from __future__ import annotations

import math
import random

from .network import INF
from .phy import BLE, ETHERNET, MICROWAVE, WIFI_AWARE, lora
from .routing.baselines import (EpidemicRouter, EtxBlackhole, EtxRouter, ProphetBlackhole,
                                ProphetRouter, QBlackhole, QRouter, SprayWaitRouter, StaticRouter)
from .routing.star import StarBlackhole, StarRouter
from .sim import P0, P2, P3, Simulator

# ------------------------------------------------------------------ registry
ROUTERS = {
    "Static": StaticRouter,
    "ETX-DV": EtxRouter,
    "Q-routing": QRouter,
    "PRoPHET": ProphetRouter,
    "Epidemic": EpidemicRouter,
    "Spray&Wait": SprayWaitRouter,
    "STAR": StarRouter,
    "STAR-noTS": lambda s, n: StarRouter(s, n, ts=False),
    "STAR-noRisk": lambda s, n: StarRouter(s, n, risk=False),
    "STAR-noE2E": lambda s, n: StarRouter(s, n, e2e=False, trust=False),
    "STAR-noSpray": lambda s, n: StarRouter(s, n, spray=False),
    "STAR-noStab": lambda s, n: StarRouter(s, n, stability=False),
    "STAR-noDecay": lambda s, n: StarRouter(s, n, decay=False),
    "STAR-noWatch": lambda s, n: StarRouter(s, n, watchdog=False),
}
BLACKHOLE = {
    "ETX-DV": EtxBlackhole, "Q-routing": QBlackhole, "PRoPHET": ProphetBlackhole,
    "STAR": StarBlackhole, "STAR-noE2E": lambda s, n: StarBlackhole(s, n, e2e=False, trust=False),
}


def make_factory(sim: Simulator, name: str):
    if name == "Static":
        tables = StaticRouter.build_tables(sim)
        return lambda s, n: StaticRouter(s, n, tables[n.id])
    base = ROUTERS[name]
    bh = BLACKHOLE.get(name, StarBlackhole if name.startswith("STAR") else None)

    def fac(s, n):
        if n.malicious and bh is not None:
            r = bh(s, n)
        else:
            r = base(s, n)
        r.name = name
        return r
    return fac


# ------------------------------------------------------------------ 7-node path
def seven_node(seed: int, router: str, n_msgs: int, warmup: float = 600.0,
               outage: float = 80.0, window: float = 600.0):
    """Replicates the TRCN v3.2 benchmark topology: a 7-node path whose only
    cross-region backbone returns `outage` seconds after the messages are queued."""
    sim = Simulator(seed)
    a2 = sim.add_node("phone", "A-phone2", 64, "A")
    a1 = sim.add_node("phone", "A-phone", 64, "A")
    sa = sim.add_node("shelter", "A-shelter", 256, "A")
    ga = sim.add_node("gateway", "A-gateway", 512, "A")
    gb = sim.add_node("gateway", "B-gateway", 512, "B")
    sb = sim.add_node("shelter", "B-shelter", 256, "B")
    b1 = sim.add_node("phone", "B-phone", 64, "B")
    sim.add_link(a2, a1, BLE, loss=0.02)
    sim.add_link(a1, sa, WIFI_AWARE, loss=0.02)
    sim.add_link(sa, ga, lora(9), loss=0.05, domain="loraA")
    bb = sim.add_link(ga, gb, MICROWAVE, loss=0.01)
    bb.forced.append((warmup, warmup + outage))
    sim.add_link(gb, sb, lora(9), loss=0.05, domain="loraB")
    sim.add_link(sb, b1, WIFI_AWARE, loss=0.02)
    sim.install_routers(make_factory(sim, router))
    ev = [(warmup, a2.id, b1.id, "private", P2, 160, 900.0) for _ in range(n_msgs)]
    sim.schedule_traffic(ev)
    sim.measure_from = warmup
    sim.run(warmup + window)
    return sim, sim.summary(warmup, warmup + 1)


# ------------------------------------------------------------------ disaster region
def _sf_for(d_km: float) -> int:
    for lim, sf in ((2.0, 7), (3.0, 8), (4.5, 9), (6.0, 10)):
        if d_km < lim:
            return sf
    return 11


def build_disaster(sim: Simulator, rng: random.Random, n_shelters: int = 4, phones: int = 3,
                   flaky_frac: float = 0.35, size_km: float = 8.0):
    """Two regions (N, S); each: gateway, coordinator, shelters (LoRa mesh), phones.
    Cross-region: a fast but intermittent microwave backbone plus a slow, stable
    LoRa relay through a hill-top repeater and (sometimes) a border shelter link."""
    regions = {}
    for r, (ox, oy) in (("N", (0.0, 0.0)), ("S", (0.0, size_km + 6.0))):
        gw = sim.add_node("gateway", f"{r}-gw", 512, r)
        co = sim.add_node("coord", f"{r}-coord", 512, r)
        sim.add_link(gw, co, ETHERNET, loss=0.001)
        gpos = (ox + size_km / 2, oy + size_km / 2)
        pos = {gw.id: gpos}
        shelters = []
        tries = 0
        while len(shelters) < n_shelters and tries < 500:
            tries += 1
            p = (ox + rng.uniform(0, size_km), oy + rng.uniform(0, size_km))
            if all(math.dist(p, pos[s.id]) > 1.5 for s in shelters) and math.dist(p, gpos) > 1.5:
                s = sim.add_node("shelter", f"{r}-sh{len(shelters)}", 256, r)
                pos[s.id] = p
                shelters.append(s)
        dom = f"lora{r}"
        lora_nodes = [gw] + shelters

        def mk_lora(u, v):
            d = math.dist(pos[u.id], pos[v.id])
            flaky = rng.random() < flaky_frac
            q = min(0.4, 0.02 + 0.25 * (d / 7.0) ** 3 + rng.uniform(0, 0.03))
            if flaky:
                up, dn = rng.uniform(90, 240), rng.uniform(90, 240)
            else:
                up, dn = rng.uniform(1800, 3600), rng.uniform(20, 60)
            return sim.add_link(u, v, lora(_sf_for(d)), loss=q, up_mean=up, down_mean=dn,
                                domain=dom, label="flaky" if flaky else "stable")
        connected = set()
        for i, u in enumerate(lora_nodes):
            for v in lora_nodes[i + 1:]:
                d = math.dist(pos[u.id], pos[v.id])
                lim = 7.0 if (u is gw or v is gw) else 5.0
                if d < lim:
                    mk_lora(u, v)
                    connected.update((u.id, v.id))
        for s in shelters:  # guarantee every shelter has at least one LoRa link
            if s.id not in connected:
                near = min((x for x in lora_nodes if x is not s), key=lambda x: math.dist(pos[s.id], pos[x.id]))
                mk_lora(s, near)
                connected.update((s.id, near.id))
        ph_all = []
        for si, s in enumerate(shelters):
            group = []
            for k in range(phones):
                ph = sim.add_node("phone", f"{s.name}-p{k}", 64, r)
                sim.add_link(ph, s, WIFI_AWARE, loss=rng.uniform(0.02, 0.08),
                             up_mean=rng.uniform(300, 900), down_mean=rng.uniform(60, 240))
                if rng.random() < 0.3 and len(shelters) > 1:
                    alt = shelters[(si + 1) % len(shelters)]
                    sim.add_link(ph, alt, WIFI_AWARE, loss=rng.uniform(0.05, 0.12),
                                 up_mean=rng.uniform(120, 300), down_mean=rng.uniform(200, 600))
                for q in group:
                    if rng.random() < 0.5:
                        sim.add_link(ph, q, BLE, loss=rng.uniform(0.03, 0.1),
                                     up_mean=rng.uniform(120, 400), down_mean=rng.uniform(120, 400))
                group.append(ph)
                ph_all.append(ph)
        regions[r] = dict(gw=gw, coord=co, shelters=shelters, phones=ph_all, pos=pos)
    gN, gS = regions["N"]["gw"], regions["S"]["gw"]
    mw = sim.add_link(gN, gS, MICROWAVE, loss=0.03, up_mean=rng.uniform(150, 400),
                      down_mean=rng.uniform(120, 300), label="backbone-mw")
    relay = sim.add_node("relay", "hill-relay", 512, "X")
    rl1 = sim.add_link(gN, relay, lora(10, name="lora_bb"), loss=0.04, up_mean=3600, down_mean=20,
                       domain="loraBB", label="backbone-relay")
    rl2 = sim.add_link(relay, gS, lora(10, name="lora_bb"), loss=0.04, up_mean=3600, down_mean=20,
                       domain="loraBB", label="backbone-relay")
    # border shelters (closest pair across regions), flaky LoRa SF11
    pN, pS = regions["N"]["pos"], regions["S"]["pos"]
    pairs = [(math.dist(pN[a.id], pS[b.id]), a, b) for a in regions["N"]["shelters"] for b in regions["S"]["shelters"]]
    if pairs:
        d, a, b = min(pairs, key=lambda x: x[0])
        if d < 10.0:
            sim.add_link(a, b, lora(11, name="lora_border"), loss=0.1, up_mean=rng.uniform(200, 400),
                         down_mean=rng.uniform(200, 400), domain="loraBorder", label="border")
    return regions, dict(mw=mw, relay=(rl1, rl2))


def disaster_traffic(rng: random.Random, regions: dict, t0: float, t1: float, rate_per_hour: float,
                     sos_frac: float = 0.1, supply_per_hour: float = 2.0):
    ev = []
    phones = regions["N"]["phones"] + regions["S"]["phones"]
    eoc = regions["S"]["coord"]
    for ph in phones:
        t = t0 + rng.expovariate(rate_per_hour / 3600.0)
        while t < t1:
            if rng.random() < sos_frac:
                dst = regions[ph.region]["coord"] if rng.random() < 0.5 else eoc
                ev.append((t, ph.id, dst.id, "sos", P0, rng.randint(80, 140), 900.0))
            else:
                if rng.random() < 0.7:
                    other = "S" if ph.region == "N" else "N"
                    pool = regions[other]["phones"]
                else:
                    pool = [p for p in regions[ph.region]["phones"] if p is not ph]
                dst = rng.choice(pool)
                ev.append((t, ph.id, dst.id, "private", P2, rng.randint(120, 480), 900.0))
            t += rng.expovariate(rate_per_hour / 3600.0)
    for r in ("N", "S"):
        for sh in regions[r]["shelters"]:
            t = t0 + rng.expovariate(supply_per_hour / 3600.0)
            while t < t1:
                ev.append((t, sh.id, eoc.id, "supply", P3, rng.randint(200, 400), 1200.0))
                t += rng.expovariate(supply_per_hour / 3600.0)
    return ev


def disaster(seed: int, router: str, rate: float = 4.0, warmup: float = 900.0,
             duration: float = 3600.0, aftershock: bool = False, blackholes: int = 0,
             n_shelters: int = 4, phones: int = 3, return_sim: bool = False):
    topo_rng = random.Random(1000 + seed)       # topology depends on seed only
    traffic_rng = random.Random(2000 + seed)
    sim = Simulator(seed)
    regions, bb = build_disaster(sim, topo_rng, n_shelters=n_shelters, phones=phones)
    # malicious shelters chosen before routers are installed
    if blackholes:
        sh = regions["N"]["shelters"] + regions["S"]["shelters"]
        for s in random.Random(3000 + seed).sample(sh, min(blackholes, len(sh))):
            s.malicious = True
    t0, t1 = warmup, warmup + duration
    if aftershock:
        tA = warmup + duration / 2
        ar = random.Random(4000 + seed)
        bb["mw"].fail_at = tA
        lora_mesh = [l for l in sim.links if l.domain is not None and l.domain.name in ("loraN", "loraS")]
        for l in ar.sample(lora_mesh, max(1, int(0.3 * len(lora_mesh)))):
            l.fail_at = tA
        victim = ar.choice(regions["N"]["shelters"] + regions["S"]["shelters"])
    fac = make_factory(sim, router)
    sim.install_routers(fac)
    if aftershock:
        sim.at(tA, sim.crash, victim, 600.0, fac)
    sim.schedule_traffic(disaster_traffic(traffic_rng, regions, t0, t1, rate))
    sim.run(t1 + 900.0 + sim.receipt_grace + 60.0)
    out = sim.summary(t0, t1)
    out.update(rate=rate, aftershock=aftershock, blackholes=blackholes)
    if return_sim:
        return sim, out, regions
    return out
