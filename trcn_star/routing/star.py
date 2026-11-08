"""STAR - Stability-aware TAR routing.

A fully local, learning next-hop policy for TRCN's TAR layer.

Prediction
    For message m at node i, destination d, remaining lifetime tau and next hop j:
        mu_ij    = h_ij + W_j + mu_j(d)            (expected delivery delay)
        var_ij   = v_ij + var_j(d)                 (delay variance)
        pi_ij    = blend(claim pi_j(d) * trust_j, end-to-end outcomes)  (not-lost prob.)
        Psi_ij   = pi_ij * Phi((tau - mu_ij) / sqrt(var_ij))            (stability)
    h_ij, v_ij come from a hop model driven by two discounted Beta posteriors per link
    (frame success f and availability u) and a learned outage-length estimate.

Decision
    P0/P1 (SOS, receipts): risk-averse lower-credible-bound estimates, no exploration;
          a P0 message is replicated over L <= L_MAX disjoint next hops when no
          single next hop reaches Psi >= PSI_TARGET.
    P2/P3: Thompson sampling over the posteriors (exploration on non-urgent traffic).
    A message is held (store-carry-forward) while the best next hop is believed down.

Learning
    * per-round fragment bitmap ACKs  -> frame-success posterior;
    * beacons / ACK presence         -> availability posterior + outage length;
    * custody ACK piggyback          -> neighbour's (mu, var, pi) for d (distance-vector);
    * authenticated E2E receipt or its absence at the source
                                     -> discounted Beta on (d, j) outcomes, and a
                                        per-neighbour trust ratio realised/claimed
                                        that bounds route-poisoning claims.
"""
from __future__ import annotations

import math

from ..network import INF
from .base import Router

SQRT2 = math.sqrt(2.0)


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / SQRT2))


class LinkPosterior:
    """Discounted Beta posteriors for one link, as seen by one endpoint."""
    __slots__ = ("af", "bf", "au", "bu", "pf", "pu", "t", "last_up", "D", "last_seq",
                 "hl_f", "hl_u")

    def __init__(self, prior_f=(4.0, 1.0), prior_u=(3.0, 1.0), hl_f=900.0, hl_u=240.0):
        self.pf, self.pu = prior_f, prior_u
        self.af, self.bf = prior_f
        self.au, self.bu = prior_u
        self.t = 0.0
        self.last_up = -INF
        self.D = 60.0
        self.last_seq = None
        self.hl_f, self.hl_u = hl_f, hl_u

    def _decay(self, now: float) -> None:
        dt = now - self.t
        if dt <= 0:
            return
        ff = 0.5 ** (dt / self.hl_f)
        fu = 0.5 ** (dt / self.hl_u)
        self.af = self.pf[0] + (self.af - self.pf[0]) * ff
        self.bf = self.pf[1] + (self.bf - self.pf[1]) * ff
        self.au = self.pu[0] + (self.au - self.pu[0]) * fu
        self.bu = self.pu[1] + (self.bu - self.pu[1]) * fu
        self.t = now

    def frames(self, ok: int, n: int, now: float) -> None:
        self._decay(now)
        self.af += ok
        self.bf += n - ok

    def avail(self, up: bool, now: float, weight: float = 1.0, beacon_period: float = 60.0) -> None:
        self._decay(now)
        if up:
            gap = now - self.last_up
            if 1.6 * beacon_period < gap < INF:
                self.D = 0.7 * self.D + 0.3 * gap
            self.au += weight
            self.last_up = now
        else:
            self.bu += weight

    # ---- point estimates
    def f_mean(self) -> float:
        return self.af / (self.af + self.bf)

    def f_lcb(self, z: float = 1.28) -> float:
        a, b = self.af, self.bf
        n = a + b
        m = a / n
        sd = math.sqrt(a * b / (n * n * (n + 1)))
        return max(0.05, m - z * sd)

    def u_mean(self) -> float:
        return self.au / (self.au + self.bu)


class StarRouter(Router):
    name = "STAR"
    piggyback_bytes = True
    ENTRY_BYTES = 11  # mailbox 4, mu 2, sigma 1, pi 1, next-hop 1, seqno 2
    AUTH_BYTES = 8    # hash-chain seqno authenticator, sent only when an entry's seqno changes
    K_ENTRIES = 24
    TAU_REF = 600.0
    PSI_TARGET = 0.95
    L_MAX = 3
    K_CLAIM = 4.0       # pseudo-counts given to a neighbour's claim
    HL_E2E = 1200.0
    STALE_HL = 600.0
    MIN_TRUST = 0.4
    EPS = 0.03       # Psi tolerance for delay tie-breaking (P1-P3)
    EPS_P0 = 0.01    # tighter tolerance for SOS

    def __init__(self, sim, node, *, ts=True, risk=True, e2e=True, trust=True, spray=True,
                 stability=True, decay=True, watchdog=True):
        super().__init__(sim, node)
        self.flags = dict(ts=ts, risk=risk, e2e=e2e, trust=trust, spray=spray,
                          stability=stability, decay=decay, watchdog=watchdog and trust)
        self.watch: dict[int, tuple] = {}      # uid -> (j, deadline)
        self.excluded: set[int] = set()
        self.fd_nh: dict[int, int] = {}        # d -> next hop of last advertised route
        self.lp: dict[int, LinkPosterior] = {}
        self.adv: dict[tuple, list] = {}       # (d, j) -> [mu, var, pi, t]
        self.backlog: dict[int, tuple] = {}    # j -> (W_all, W_p0, t)
        self.e2e: dict[tuple, list] = {}       # (d, j) -> [s, n, t]
        self.tr: dict[int, list] = {}          # j -> [realised, claimed]
        self.pending: dict[int, tuple] = {}    # uid -> (d, j, claim)
        self.dseq: dict[int, int] = {}
        self._nh_of: dict[tuple, int] = {}
        self._table_cache = (None, None)
        if not ts:
            self.name = "STAR-noTS"

    # ------------------------------------------------------------ link model
    def post(self, link) -> LinkPosterior:
        p = self.lp.get(link.id)
        if p is None:
            if self.flags["decay"]:
                p = LinkPosterior()
            else:
                p = LinkPosterior(hl_f=1e12, hl_u=1e12)
            self.lp[link.id] = p
        return p

    def p_up_now(self, link, now: float) -> float:
        p = self.post(link)
        u = p.u_mean()
        silence = now - p.last_up
        grace = 1.6 * self.sim.beacon_period
        if silence <= grace:
            return u
        if silence == INF:
            return 0.0
        return u * math.exp(-(silence - grace) / max(10.0, p.D))

    def hop(self, link, size: int, now: float, mode: str):
        """Return (mean, var) of the hop time for a message of `size` bytes."""
        p = self.post(link)
        if mode == "sample":
            f = max(0.05, self.sim.rng.betavariate(p.af, p.bf))
        elif mode == "lcb":
            f = p.f_lcb()
        else:
            f = max(0.05, p.f_mean())
        n = link.medium.frames_for(size)
        tf = link.frame_time()
        ta = link.frame_time(40)
        t_tx = (n / f) * tf + ta * (1.0 + 3.0 * (1.0 - f))
        v_tx = n * (1.0 - f) / (f * f) * tf * tf
        u = self.p_up_now(link, now)
        if mode == "sample" and u > 0:
            # Thompson draw of availability, scaled by the silence-based belief
            um = p.u_mean()
            u = min(1.0, u * self.sim.rng.betavariate(p.au, p.bu) / um)
        wait = (1.0 - u) * p.D
        v_wait = (1.0 - u) * p.D * p.D
        own = self._own_backlog(link, size)
        return t_tx + wait + own, v_tx + v_wait + 1.0, u

    def _own_backlog(self, link, size) -> float:
        k = sum(1 for c in self.node.queue if c.msg.priority <= 1)
        return 0.0 if k <= 1 else 0.25 * k * link.medium.frames_for(size) * link.frame_time()

    def my_backlog(self):
        tot = 0.0
        p0 = 0.0
        for c in self.node.queue:
            l = self.node.links[0] if self.node.links else None
            if l is None:
                break
            t = l.medium.frames_for(c.msg.size) * l.frame_time()
            tot += t
            if c.msg.priority == 0:
                p0 += t
        nif = max(1, len({l.iface for l in self.node.links}))
        return tot / nif, p0 / nif

    # ------------------------------------------------------------ reliability
    def trust(self, j: int) -> float:
        """Calibration of j's promises: realised / claimed deliveries (shrunk to 1)."""
        if not self.flags["trust"]:
            return 1.0
        r, c = self.tr.get(j, (0.0, 0.0))
        return min(1.0, (r + 2.0) / (c + 2.0))

    def _evidence(self, j: int, realised: float, claimed: float) -> None:
        t = self.tr.setdefault(j, [0.0, 0.0])
        t[0] += realised
        t[1] += claimed
        if self.flags["trust"] and j not in self.excluded and self.trust(j) < self.MIN_TRUST:
            self.excluded.add(j)
            # forget routes learned through j, including inflated sequence numbers
            for k in [k for k in self.adv if k[1] == j]:
                del self.adv[k]
            for d in [d for d, nh in self.fd_nh.items() if nh == j]:
                self.fd.pop(d, None)
                self.fd_nh.pop(d, None)
            self._table_cache = (None, None)

    def _e2e_counts(self, d, j, now):
        e = self.e2e.get((d, j))
        if e is None or not self.flags["e2e"]:
            return 0.0, 0.0
        f = 0.5 ** ((now - e[2]) / self.HL_E2E)
        return e[0] * f, e[1] * f

    def pi_post(self, d, j, claim, now):
        """Posterior (a, b) for the not-lost probability via j."""
        c = claim * self.trust(j)
        s, n = self._e2e_counts(d, j, now)
        a = self.K_CLAIM * c + s + 1e-3
        b = self.K_CLAIM * (1.0 - c) + (n - s) + 1e-3
        return a, b

    # ------------------------------------------------------------ scoring
    def downstream(self, d, j, prio, now):
        """Neighbour j's advertised (mu, var, claim) for destination d."""
        if j == d:
            return 0.0, 0.0, 0.999
        e = self.adv.get((d, j))
        if e is None:
            return None
        if not self.feasible(d, e[4], e[0]):
            return None
        age = now - e[3]
        if age > 3 * self.STALE_HL:
            return None
        mu, var, pi = e[0], e[1], e[2]
        b = self.backlog.get(j)
        if b is not None:
            mu += b[1] if prio == 0 else b[0]
        refresh = 2.0 * self.sim.beacon_period
        late = max(0.0, age - refresh)
        pi *= 0.5 ** (late / self.STALE_HL)
        var += (0.2 * late) ** 2
        return mu, var, pi

    def score(self, link, j, d, size, prio, tau, now, mode):
        ds = self.downstream(d, j, prio, now)
        if ds is None:
            return None
        if j != d and self.trust(j) < self.MIN_TRUST:
            return None
        h, hv, u = self.hop(link, size, now, mode)
        mu = h + ds[0]
        var = hv + ds[1]
        a, b = self.pi_post(d, j, ds[2], now) if j != d else (999.0, 1.0)
        pi = a / (a + b)
        if self.flags["stability"]:
            psi = pi * phi((tau - mu) / math.sqrt(var))
        else:
            psi = pi * (1.0 if mu < tau else 0.0)
        return psi, mu, var, pi, u, ds[2]

    def _ranked(self, copy, now, mode, exclude_given=False, ignore_path=False):
        m = copy.msg
        tau = m.expiry - now
        out = []
        for l, j in self.neighbours():
            if not ignore_path and j in copy.path and j != m.dst:
                continue
            if exclude_given and j in copy.given:
                continue
            s = self.score(l, j, m.dst, m.size, m.priority, tau, now, mode)
            if s is None:
                continue
            out.append((s, l, j))
        if self.flags["stability"]:
            # epsilon-lexicographic: maximise Psi, break near-ties by expected delay
            if out:
                top = max(x[0][0] for x in out)
                eps = self.EPS_P0 if m.priority == 0 else self.EPS
                out.sort(key=lambda x: (0 if x[0][0] >= top - eps else 1, x[0][1] if x[0][0] >= top - eps else -x[0][0]))
        else:
            out.sort(key=lambda x: (x[0][1] if x[0][0] > 0 else INF))
        return out

    def _mode(self, prio):
        if prio >= 2 and self.flags["ts"]:
            return "sample"
        return "lcb" if self.flags["risk"] else "mean"

    def decide(self, copy, now):
        m = copy.msg
        if m.expiry <= now:
            return []
        mode = self._mode(m.priority)
        ranked = self._ranked(copy, now, mode, exclude_given=bool(copy.given))
        if not ranked and copy.given:
            ranked = self._ranked(copy, now, mode)
        if not ranked:
            return []
        (psi, mu, var, pi, u, claim), l, j = ranked[0]
        if psi < 1e-4:
            return []
        # store-carry-forward: hold while the preferred link is believed down
        if u < 0.3 and j != m.dst:
            alt = [r for r in ranked[1:] if r[0][4] >= 0.3 and r[0][0] >= 0.5 * psi]
            if not alt:
                return []
            (psi, mu, var, pi, u, claim), l, j = alt[0]
        if u < 0.15:
            return []
        # replication for SOS at the source
        if (m.priority == 0 and self.flags["spray"] and copy.hops == 0 and copy.tokens == 1
                and not copy.given and m.src == self.node.id):
            if psi < self.PSI_TARGET and len(ranked) > 1:
                L = math.ceil(math.log(1 - self.PSI_TARGET) / math.log(max(1e-6, 1 - psi)))
                copy.tokens = max(1, min(self.L_MAX, L, len(ranked)))
        if m.src == self.node.id and copy.hops == 0 and m.pred_psi is None:
            mean_s = self.score(l, j, m.dst, m.size, m.priority, m.expiry - now, now, "mean")
            m.pred_psi = mean_s[0] if mean_s else psi
            self.sim.stats.calib.append((m.pred_psi, m.priority, m.uid))
        give = 1 if copy.tokens <= 1 else max(1, copy.tokens // 2)
        if copy.tokens > 1 and j == m.dst:
            give = copy.tokens
        return [(l, j, give)]

    # ------------------------------------------------------------ advertisement
    def best_for(self, d, now, exclude=-1):
        best = None
        for l, j in self.neighbours():
            if j == exclude:
                continue
            sc = self.score(l, j, d, 500, 2, self.TAU_REF, now, "mean")
            if sc is None:
                continue
            key = (sc[0], -sc[1])
            if best is None or key > best[0]:
                best = (key, sc, j)
        if best is None or best[1][0] <= 1e-4:
            return None
        sc = best[1]
        j = best[2]
        sq = self.adv[(d, j)][4] if j != d else self.dseq.get(d, 0)
        return (sc[1], sc[2], min(0.99, sc[3]), j, sq)

    def table(self, now):
        t, tab = self._table_cache
        if t is not None and now - t < 10.0:
            return tab
        dsts = {d for (d, _j) in self.adv} | {j for _l, j in self.neighbours()}
        dsts.discard(self.node.id)
        tab = {}
        for d in dsts:
            e = self.best_for(d, now)
            if e is not None:
                tab[d] = e
        self._table_cache = (now, tab)
        return tab

    def _advertise(self, tab, keys):
        out = {}
        for d in keys:
            mu, var, pi, nh, sq = tab[d]
            self.note_advertised(d, sq, mu)
            self.fd_nh[d] = nh
            out[d] = (mu, var, pi, nh, sq)
        return out

    def beacon_payload(self, now):
        self._nb = getattr(self, "_nb", 0) + 1
        if self._nb % self.SEQ_EVERY == 1:
            self.seq += 1
        self._table_cache = (None, None)
        tab = self.table(now)
        keys = self.rotating(sorted(tab))
        e = self._advertise(tab, keys)
        e[self.node.id] = (0.0, 0.0, 1.0, -1, self.seq)
        last = self.__dict__.setdefault("_last_seq_sent", {})
        changed = 0
        for d, v in e.items():
            if last.get(d) != v[4]:
                changed += 1
                last[d] = v[4]
        w = self.my_backlog()
        return {"adv": e, "W": w}, self.beacon_size(len(e)) + 4 + self.AUTH_BYTES * changed

    def on_beacon(self, link, frm, payload, seq, now):
        super().on_beacon(link, frm, payload, seq, now)
        p = self.post(link)
        if p.last_seq is not None and seq > p.last_seq + 1:
            p.avail(False, now, weight=min(3, seq - p.last_seq - 1), beacon_period=self.sim.beacon_period)
        p.avail(True, now, beacon_period=self.sim.beacon_period)
        p.last_seq = seq
        if payload:
            for d, (mu, var, pi, nh, sq) in payload.get("adv", {}).items():
                if d == self.node.id or not self._seq_authentic(d, sq):
                    continue
                if d == frm:
                    self.dseq[d] = max(self.dseq.get(d, 0), sq)
                    continue
                if nh == self.node.id:          # poisoned reverse
                    self.adv.pop((d, frm), None)
                    continue
                if frm in self.excluded:
                    continue
                self._nh_of[(d, frm)] = nh
                self.adv[(d, frm)] = [max(0.0, mu), max(0.0, var), min(0.99, max(0.0, pi)), now, sq]
            if "W" in payload:
                self.backlog[frm] = (payload["W"][0], payload["W"][1], now)

    def piggyback(self, dst, prio, now, asker=-1):
        if dst == self.node.id:
            return (0.0, 0.0, 1.0, self.my_backlog())
        e = self.best_for(dst, now, exclude=asker)
        if not e:
            return None
        self.note_advertised(dst, e[4], e[0])
        return (e[0], e[1], e[2], e[3], e[4], self.my_backlog())

    # ------------------------------------------------------------ learning
    def on_round(self, link, nbr, sent, got, acked, now):
        p = self.post(link)
        if acked:
            p.frames(got, sent, now)
            p.avail(True, now, beacon_period=self.sim.beacon_period)
        else:
            p.avail(False, now, weight=0.5, beacon_period=self.sim.beacon_period)

    def on_hop_result(self, copy, link, nbr, result, piggy, hop_time, now):
        m = copy.msg
        if piggy is not None and nbr != m.dst:
            if len(piggy) == 6:
                mu, var, pi, nh, sq, w = piggy
                if nbr not in self.excluded and self._seq_authentic(m.dst, sq):
                    self.adv[(m.dst, nbr)] = [mu, var, min(0.99, pi), now, sq]
                    self._nh_of[(m.dst, nbr)] = nh
            else:
                w = piggy[3]
            self.backlog[nbr] = (w[0], w[1], now)
        if result == "reject":
            b = self.backlog.get(nbr, (0.0, 0.0, now))
            self.backlog[nbr] = (max(b[0], 300.0), b[1], now)
        if result == "accept" and nbr != m.dst:
            ds = self.adv.get((m.dst, nbr))
            if m.src == self.node.id and copy.hops == 0 and m.kind != "receipt" \
                    and m.uid not in self.pending:
                claim = ds[2] if ds else 0.5
                self.pending[m.uid] = [m.dst, nbr, claim, False]
                m.first_hop = nbr
                if self.flags["e2e"] and ds is not None:
                    # a route claim is a falsifiable commitment: expect the receipt in time
                    rtt = 2.0 * (ds[0] + 2.0 * math.sqrt(ds[1])) + 60.0
                    rtt = min(max(rtt, 120.0), m.expiry - now + self.sim.receipt_grace)
                    self.sim.at(now + rtt, self._e2e_check, m.uid)
            if self.flags["watchdog"] and ds is not None:
                self._watch(m, link, nbr, ds, now)

    def _e2e_add(self, d, j, s, n, now):
        e = self.e2e.get((d, j))
        if e is None:
            e = [0.0, 0.0, now]
        f = 0.5 ** ((now - e[2]) / self.HL_E2E)
        e[0] = e[0] * f + s
        e[1] = e[1] * f + n
        e[2] = now
        self.e2e[(d, j)] = e

    def _e2e_check(self, uid):
        rec = self.pending.get(uid)
        if rec is None or rec[3] or self.node.router is not self:
            return
        d, j, claim, _ = rec
        rec[3] = True                       # provisional failure; retracted by a late receipt
        now = self.sim.now
        self._e2e_add(d, j, 0.0, 1.0, now)
        self._evidence(j, 0.0, claim)

    def on_e2e(self, msg, ok, delay, now):
        rec = self.pending.pop(msg.uid, None)
        if rec is None:
            return
        d, j, claim, counted = rec
        self._e2e_add(d, j, 1.0 if ok else 0.0, 0.0 if counted else 1.0, now)
        self._evidence(j, 1.0 if ok else 0.0, 0.0 if counted else claim)

    def _seq_authentic(self, d, sq) -> bool:
        """Destination-authenticated sequence numbers (SAODV-style hash chain):
        a relay cannot advertise a seqno that destination d has not yet released."""
        r = self.sim.nodes[d].router
        return r is None or sq <= getattr(r, "seq", sq)

    # ------------------------------------------------------------ watchdog
    def _watch(self, m, link, nbr, ds, now):
        """Watch nbr forward m if its advertised next hop shares our LoRa channel."""
        if link.domain is None:
            return
        nh = self._nh_of.get((m.dst, nbr))
        if nh is None or nh < 0 or nh not in link.domain.members:
            return
        b = self.backlog.get(nbr, (0.0, 0.0, now))
        wait = b[1] if m.priority == 0 else b[0]
        deadline = now + min(m.expiry - now, ds[0] + wait + 3.0 * math.sqrt(ds[1]) + 90.0)
        self.watch[m.uid] = (nbr, deadline)
        self.sim.at(deadline, self._watch_check, m.uid, deadline)

    def on_overhear(self, sender, uid, now):
        w = self.watch.get(uid)
        if w is not None and w[0] == sender:
            del self.watch[uid]
            self._evidence(sender, 1.0, 1.0)

    def _watch_check(self, uid, deadline):
        w = self.watch.get(uid)
        if w is None or w[1] != deadline or self.node.router is not self:
            return
        del self.watch[uid]
        self._evidence(w[0], 0.0, 1.0)


class StarBlackhole(StarRouter):
    """Compromised node: advertises perfect, sequence-inflated routes everywhere
    (route poisoning, T10) and silently drops what it receives (sim.malicious).
    It names a random honest neighbour as next hop so that it is not trivially
    excluded from watchdog observation."""

    def _fake_nh(self, d):
        nb = [l.other(self.node.id) for l in self.node.links if l.domain is not None]
        nb = nb or [l.other(self.node.id) for l in self.node.links]
        return nb[(d * 7919 + self.node.id) % len(nb)] if nb else -1

    def _latest_seq(self, d):
        r = self.sim.nodes[d].router
        return getattr(r, "seq", 0) if r is not None else 0

    def table(self, now):
        return {n.id: (0.0, 0.0, 0.99, self._fake_nh(n.id), self._latest_seq(n.id))
                for n in self.sim.nodes if n.id != self.node.id}

    def best_for(self, d, now, exclude=-1):
        return (0.0, 0.0, 0.99, self._fake_nh(d), self._latest_seq(d))

    def my_backlog(self):
        return (0.0, 0.0)
