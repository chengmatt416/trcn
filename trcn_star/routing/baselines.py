"""Baseline routing policies.

* StaticRouter   - TRCN v3.2 behaviour: preloaded shortest next hops, never updated.
* EtxRouter      - distance vector with an expected-transmission-time metric
                   (ETX/ETT; De Couto et al., 2003), beacon-probed link quality.
* QRouter        - Q-routing (Boyan & Littman, 1994) with epsilon-greedy exploration.
* ProphetRouter  - PRoPHET probabilistic routing (Lindgren et al., 2003), multi-copy.
* EpidemicRouter - epidemic replication (Vahdat & Becker, 2000) with summary vectors.
* SprayWaitRouter- binary spray-and-wait (Spyropoulos et al., 2005).
"""
from __future__ import annotations

import heapq
import math

from ..network import INF
from .base import Router

COST_CAP = 1e5


def nominal_hop_cost(link, size: int = 500) -> float:
    n = link.medium.frames_for(size)
    return n * link.frame_time() / max(0.05, 1 - link.loss)


# ============================================================== static
class StaticRouter(Router):
    name = "Static"
    uses_beacons = False

    def __init__(self, sim, node, table=None):
        super().__init__(sim, node)
        self.table = table if table is not None else {}

    @staticmethod
    def build_tables(sim) -> dict:
        """Dijkstra on nominal hop cost, computed once from the t=0 topology."""
        adj: dict[int, list] = {n.id: [] for n in sim.nodes}
        for l in sim.links:
            c = nominal_hop_cost(l)
            adj[l.a].append((l.b, l, c))
            adj[l.b].append((l.a, l, c))
        tables = {}
        for s in sim.nodes:
            dist = {s.id: 0.0}
            first = {}
            pq = [(0.0, s.id, None)]
            while pq:
                d, u, fl = heapq.heappop(pq)
                if d > dist.get(u, INF):
                    continue
                if fl is not None and u not in first:
                    first[u] = fl
                for v, l, c in adj[u]:
                    nd = d + c
                    if nd < dist.get(v, INF):
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v, fl if fl is not None else (l, v)))
            tables[s.id] = first
        return tables

    def decide(self, copy, now):
        hop = self.table.get(copy.msg.dst)
        if hop is None:
            return []
        link, nbr = hop
        return [(link, nbr, copy.tokens)]


# ============================================================== ETX distance vector
class EtxRouter(Router):
    name = "ETX-DV"
    WINDOW = 10

    def __init__(self, sim, node):
        super().__init__(sim, node)
        self.rx_hist: dict[int, list] = {}     # link id -> list of (seq) received in window
        self.last_seq: dict[int, int] = {}
        self.ratio: dict[int, float] = {}
        self.adv: dict[tuple, tuple] = {}      # (dst, link id) -> (cost, t, seq)
        self.dseq: dict[int, int] = {}         # direct neighbours' own seqnos

    def link_cost(self, link) -> float:
        r = self.ratio.get(link.id, 0.0)
        if r <= 0.05:
            return COST_CAP
        etx = 1.0 / (r * r)
        return etx * link.medium.frames_for(500) * link.frame_time()

    def route(self, dst: int, now: float, exclude=()):
        best, arg = COST_CAP, None
        for l, j in self.neighbours():
            if j in exclude and j != dst:
                continue
            if not self.alive(l, now):
                continue
            if j == dst:
                c, sq = self.link_cost(l), None
            else:
                e = self.adv.get((dst, l.id))
                if e is None or now - e[1] > 6 * self.sim.beacon_period:
                    continue
                if not self.feasible(dst, e[2], e[0]):
                    continue
                c, sq = self.link_cost(l) + e[0], e[2]
            if c < best:
                best, arg = c, (l, j, sq)
        return best, arg

    def beacon_payload(self, now):
        self.node_beacons = getattr(self, "node_beacons", 0) + 1
        if self.node_beacons % self.SEQ_EVERY == 1:
            self.seq += 1
        dsts = sorted({d for (d, _l) in self.adv} | {j for _l, j in self.neighbours()})
        entries = {self.node.id: (0.0, -1, self.seq)}
        for d in self.rotating(dsts):
            if d == self.node.id:
                continue
            c, arg = self.route(d, now)
            if c < COST_CAP:
                sq = arg[2] if arg[2] is not None else self.dseq.get(d, 0)
                entries[d] = (c, arg[1], sq)
                self.note_advertised(d, sq, c)
        return entries, self.beacon_size(len(entries))

    def on_beacon(self, link, frm, payload, seq, now):
        super().on_beacon(link, frm, payload, seq, now)
        last = self.last_seq.get(link.id)
        h = self.rx_hist.setdefault(link.id, [])
        if last is not None and seq > last:
            h.extend([0] * min(self.WINDOW, seq - last - 1))
        h.append(1)
        del h[:-self.WINDOW]
        self.last_seq[link.id] = seq
        self.ratio[link.id] = sum(h) / len(h) if len(h) >= 3 else 0.5 * sum(h) / len(h)
        if payload:
            for d, (c, nh, sq) in payload.items():
                if d == self.node.id:
                    continue
                if d == frm:
                    self.dseq[d] = max(self.dseq.get(d, 0), sq)
                if nh == self.node.id:
                    self.adv.pop((d, link.id), None)   # poisoned reverse
                else:
                    self.adv[(d, link.id)] = (c, now, sq)

    def decide(self, copy, now):
        _c, arg = self.route(copy.msg.dst, now, exclude=copy.path)
        if arg is None:
            return []
        return [(arg[0], arg[1], 1)]


class EtxBlackhole(EtxRouter):
    def beacon_payload(self, now):
        dsts = [n.id for n in self.sim.nodes if n.id != self.node.id]
        e = {d: (0.0, -1, 10 ** 6) for d in self.rotating(dsts)}
        return e, self.beacon_size(len(e))


# ============================================================== Q-routing
class QRouter(Router):
    name = "Q-routing"
    piggyback_bytes = True
    ENTRY_BYTES = 7
    ETA = 0.5
    EPS = 0.1

    def __init__(self, sim, node):
        super().__init__(sim, node)
        self.Q: dict[tuple, float] = {}   # (dst, link id) -> estimated delivery time
        self.hop: dict[int, float] = {}   # link id -> EWMA hop time

    def hop_est(self, link) -> float:
        return self.hop.get(link.id, nominal_hop_cost(link))

    def V(self, dst, exclude=(), arg=False):
        if dst == self.node.id:
            return (0.0, -1) if arg else 0.0
        best, nh = COST_CAP, -1
        for l, j in self.neighbours():
            if j in exclude and j != dst:
                continue
            q = self.hop_est(l) if j == dst else self.Q.get((dst, l.id))
            if q is not None and q < best:
                best, nh = q, j
        return (best, nh) if arg else best

    def _update(self, dst, link, target):
        k = (dst, link.id)
        self.Q[k] = target if k not in self.Q else self.Q[k] + self.ETA * (target - self.Q[k])

    def beacon_payload(self, now):
        dsts = sorted({d for (d, _l) in self.Q} | {j for _l, j in self.neighbours()})
        e = {}
        for d in self.rotating(dsts):
            v, nh = self.V(d, arg=True)
            if v < COST_CAP:
                e[d] = (v, nh)
        return e, self.beacon_size(len(e))

    def on_beacon(self, link, frm, payload, seq, now):
        super().on_beacon(link, frm, payload, seq, now)
        if payload:
            for d, (v, nh) in payload.items():
                if d == self.node.id:
                    continue
                if nh == self.node.id:
                    self.Q.pop((d, link.id), None)
                else:
                    self._update(d, link, self.hop_est(link) + v)

    def piggyback(self, dst, prio, now, asker=-1):
        return self.V(dst)

    def decide(self, copy, now):
        dst = copy.msg.dst
        cands = []
        for l, j in self.neighbours():
            if j in copy.path and j != dst:
                continue
            q = self.hop_est(l) if j == dst else self.Q.get((dst, l.id))
            if q is not None and q < COST_CAP:
                cands.append((q, l, j))
        if not cands:
            for l, j in self.neighbours():
                q = self.hop_est(l) if j == dst else self.Q.get((dst, l.id))
                if q is not None and q < COST_CAP:
                    cands.append((q, l, j))
        if not cands:
            return []
        if self.sim.rng.random() < self.EPS:
            _q, l, j = self.sim.rng.choice(cands)
        else:
            _q, l, j = min(cands, key=lambda x: x[0])
        return [(l, j, 1)]

    def on_hop_result(self, copy, link, nbr, result, piggy, hop_time, now):
        dst = copy.msg.dst
        if result in ("accept", "dup"):
            self.hop[link.id] = 0.7 * self.hop_est(link) + 0.3 * hop_time
            if nbr != dst and piggy is not None:
                self._update(dst, link, hop_time + piggy)
        elif result == "fail":
            self.hop[link.id] = self.hop_est(link) + 0.3 * hop_time
            if nbr != dst:
                k = (dst, link.id)
                if k in self.Q:
                    self.Q[k] += self.ETA * hop_time


class QBlackhole(QRouter):
    def beacon_payload(self, now):
        dsts = [n.id for n in self.sim.nodes if n.id != self.node.id]
        e = {d: (0.0, -1) for d in self.rotating(dsts)}
        return e, self.beacon_size(len(e))

    def piggyback(self, dst, prio, now, asker=-1):
        return 0.0


# ============================================================== multi-copy family
class _MultiCopy(Router):
    multi_copy = True
    keeps_copy = True
    SV = 24  # summary-vector hashes per beacon (4 bytes each)

    def __init__(self, sim, node):
        super().__init__(sim, node)
        self.nbr_has: dict[int, set] = {}

    def summary_vector(self):
        ids = [c.msg.uid for c in sorted(self.node.queue, key=lambda c: -c.arrived)[: self.SV]]
        return ids

    def on_beacon(self, link, frm, payload, seq, now):
        super().on_beacon(link, frm, payload, seq, now)
        if payload and "sv" in payload:
            s = self.nbr_has.setdefault(frm, set())
            s.update(payload["sv"])
            if len(s) > 2000:
                self.nbr_has[frm] = set(payload["sv"])

    def candidates(self, copy, now):
        out = []
        for l, j in self.neighbours():
            if j in copy.given or not self.alive(l, now):
                continue
            if copy.msg.uid in self.nbr_has.get(j, ()):  # neighbour already has it
                continue
            out.append((l, j))
        return out


class EpidemicRouter(_MultiCopy):
    name = "Epidemic"

    def initial_tokens(self, msg):
        return 0

    def beacon_payload(self, now):
        sv = self.summary_vector()
        return {"sv": sv}, self.BEACON_HEADER + 4 * len(sv)

    def decide(self, copy, now):
        cands = self.candidates(copy, now)
        for l, j in cands:
            if j == copy.msg.dst:
                return [(l, j, 0)]
        return [(l, j, 0) for l, j in cands]


class SprayWaitRouter(_MultiCopy):
    name = "Spray&Wait"
    keeps_copy = False
    L = 6

    def initial_tokens(self, msg):
        return self.L

    def beacon_payload(self, now):
        sv = self.summary_vector()
        return {"sv": sv}, self.BEACON_HEADER + 4 * len(sv)

    def decide(self, copy, now):
        cands = self.candidates(copy, now)
        for l, j in cands:
            if j == copy.msg.dst:
                return [(l, j, copy.tokens)]
        if copy.tokens > 1:
            return [(l, j, copy.tokens // 2) for l, j in cands]
        return []


class ProphetRouter(_MultiCopy):
    """PRoPHET with the parameters of Lindgren et al. (2003)."""
    name = "PRoPHET"
    P_INIT, BETA, GAMMA, UNIT = 0.75, 0.25, 0.98, 30.0
    ENTRY_BYTES = 5

    def __init__(self, sim, node):
        super().__init__(sim, node)
        self.P: dict[int, float] = {}
        self.t_age = 0.0
        self.nbr_P: dict[int, dict] = {}

    def _age(self, now):
        k = (now - self.t_age) / self.UNIT
        if k > 0:
            f = self.GAMMA ** k
            for d in self.P:
                self.P[d] *= f
            self.t_age = now

    def beacon_payload(self, now):
        self._age(now)
        keys = sorted(d for d, p in self.P.items() if p > 0.01)
        e = {d: self.P[d] for d in self.rotating(keys)}
        sv = self.summary_vector()
        return {"P": e, "sv": sv}, self.beacon_size(len(e)) + 4 * len(sv)

    def on_beacon(self, link, frm, payload, seq, now):
        self._age(now)
        super().on_beacon(link, frm, payload, seq, now)
        p = self.P.get(frm, 0.0)
        self.P[frm] = p + (1 - p) * self.P_INIT
        if payload and "P" in payload:
            nb = self.nbr_P.setdefault(frm, {})
            nb.update(payload["P"])
            pij = self.P[frm]
            for d, pjd in payload["P"].items():
                if d == self.node.id:
                    continue
                self.P[d] = max(self.P.get(d, 0.0), pij * pjd * self.BETA)

    def decide(self, copy, now):
        dst = copy.msg.dst
        cands = self.candidates(copy, now)
        for l, j in cands:
            if j == dst:
                return [(l, j, 0)]
        mine = self.P.get(dst, 0.0)
        good = [(self.nbr_P.get(j, {}).get(dst, 0.0), l, j) for l, j in cands]
        good = [g for g in good if g[0] > mine]
        good.sort(key=lambda g: -g[0])
        return [(l, j, 0) for _p, l, j in good]


class ProphetBlackhole(ProphetRouter):
    def beacon_payload(self, now):
        dsts = [n.id for n in self.sim.nodes if n.id != self.node.id]
        e = {d: 1.0 for d in self.rotating(dsts)}
        return {"P": e, "sv": []}, self.beacon_size(len(e))
