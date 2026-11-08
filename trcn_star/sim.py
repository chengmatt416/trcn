"""Discrete-event simulator of TRCN store-carry-forward messaging.

Models (all synthetic):
* TDF fragmentation with selective-repeat fragment ACKs (bitmap) per exchange;
* per-hop custody transfer, end-to-end authenticated RECEIPT messages;
* strict-priority queues (P0..P3) with capacity and lower-priority eviction;
* periodic, *airtime-accounted* control beacons whose contents are produced by the
  routing policy under test;
* shared collision domains with probabilistic carrier sense;
* scheduled link outages, permanent failures and node crashes (RAM custody loss);
* blackhole (route-poisoning) nodes.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field
from typing import Callable

from .network import Domain, Link, Tx, INF
from .phy import ack_bytes

P0, P1, P2, P3 = 0, 1, 2, 3


def node_id_of(ex: dict) -> int:
    return ex["node"].id
CRYPTO_OVERHEAD = 52 + 140  # TME envelope + lab E2EE material (spec section 3)


@dataclass
class Message:
    uid: int
    kind: str
    src: int
    dst: int
    priority: int
    size: int
    created: float
    expiry: float
    receipt_for: int = -1
    delivered_at: float | None = None
    confirmed_at: float | None = None
    resolved: bool = False
    pred_psi: float | None = None
    first_hop: int | None = None


class Copy:
    __slots__ = ("msg", "tokens", "path", "not_before", "fails", "arrived", "given", "hops")

    def __init__(self, msg: Message, tokens: int, path: tuple, arrived: float, hops: int = 0):
        self.msg = msg
        self.tokens = tokens
        self.path = path
        self.not_before = 0.0
        self.fails = 0
        self.arrived = arrived
        self.given: set[int] = set()
        self.hops = hops


class Node:
    def __init__(self, nid: int, kind: str, name: str, cap: int, region: str = ""):
        self.id = nid
        self.kind = kind
        self.name = name
        self.cap = cap
        self.region = region
        self.links: list[Link] = []
        self.busy: dict[str, bool] = {}
        self.queue: list[Copy] = []
        self.alive = True
        self.malicious = False
        self.router = None
        self.seen: set[int] = set()
        self.partials: dict[int, tuple[set, float]] = {}
        self.decisions: dict[int, str] = {}
        self.next_wake = INF
        self.beacon_seq = 0
        self.epoch = 0

    def holds(self, uid: int) -> bool:
        return any(c.msg.uid == uid for c in self.queue)

    def ifaces(self) -> dict[str, list[Link]]:
        out: dict[str, list[Link]] = {}
        for l in self.links:
            out.setdefault(l.iface, []).append(l)
        return out


@dataclass
class Stats:
    data_airtime: float = 0.0
    ack_airtime: float = 0.0
    ctrl_airtime: float = 0.0
    frames_sent: int = 0
    exchanges: int = 0
    exchange_fail: int = 0
    custody_transfers: int = 0
    expired_copies: int = 0
    evicted: int = 0
    blackholed: int = 0
    crash_lost: int = 0
    duplicates_at_dst: int = 0
    calib: list = field(default_factory=list)  # (psi, priority, uid)


class Simulator:
    ROUNDS_PER_EXCHANGE = 4
    POLL = 5.0
    MAX_SCAN = 24
    PARTIAL_TTL = 120.0

    def __init__(self, seed: int, beacon_period: float = 60.0, carrier_sense: float = 0.9):
        self.rng = random.Random(seed)
        self.seed = seed
        self.now = 0.0
        self._ev: list = []
        self._seq = 0
        self.nodes: list[Node] = []
        self.links: list[Link] = []
        self.domains: dict[str, Domain] = {}
        self.messages: dict[int, Message] = {}
        self._uid = 0
        self.stats = Stats()
        self.beacon_period = beacon_period
        self.carrier_sense = carrier_sense
        self.receipt_grace = 300.0
        self.measure_from = 0.0
        self.router_name = ""

    # ------------------------------------------------------------- building
    def add_node(self, kind: str, name: str, cap: int, region: str = "") -> Node:
        n = Node(len(self.nodes), kind, name, cap, region)
        self.nodes.append(n)
        return n

    def domain(self, name: str) -> Domain:
        if name not in self.domains:
            self.domains[name] = Domain(name, self.carrier_sense)
        return self.domains[name]

    def add_link(self, a: Node, b: Node, medium, *, domain: str | None = None, **kw) -> Link:
        l = Link(len(self.links), a.id, b.id, medium, random.Random(self.rng.random()),
                 domain=self.domain(domain) if domain else None, **kw)
        if l.domain is not None:
            l.domain.members.update((a.id, b.id))
        self.links.append(l)
        a.links.append(l)
        b.links.append(l)
        return l

    def install_routers(self, factory: Callable) -> None:
        for n in self.nodes:
            n.router = factory(self, n)
        self.router_name = self.nodes[0].router.name
        for n in self.nodes:
            if n.router.uses_beacons:
                self.at(self.rng.uniform(0, self.beacon_period), self._beacon, n)

    # ------------------------------------------------------------- events
    def at(self, t: float, fn, *args) -> None:
        self._seq += 1
        heapq.heappush(self._ev, (t, self._seq, fn, args))

    def run(self, until: float) -> None:
        while self._ev and self._ev[0][0] <= until:
            t, _, fn, args = heapq.heappop(self._ev)
            self.now = t
            fn(*args)
        self.now = until

    # ------------------------------------------------------------- traffic
    def originate(self, src: Node, dst: Node, kind: str, priority: int, payload: int,
                  ttl: float = 900.0, receipt_for: int = -1) -> Message:
        self._uid += 1
        m = Message(self._uid, kind, src.id, dst.id, priority, payload + CRYPTO_OVERHEAD,
                    self.now, self.now + ttl, receipt_for)
        self.messages[m.uid] = m
        if not src.alive:
            m.resolved = True
            return m
        src.seen.add(m.uid)
        c = Copy(m, src.router.initial_tokens(m), (src.id,), self.now)
        self._enqueue(src, c)
        if kind != "receipt":
            self.at(m.expiry + self.receipt_grace, self._resolve, m)
        self.wake(src, 0.0)
        return m

    def schedule_traffic(self, events: list) -> None:
        """events: list of (t, src, dst, kind, prio, payload, ttl)."""
        for (t, s, d, k, p, size, ttl) in events:
            self.at(t, self._gen, s, d, k, p, size, ttl)

    def _gen(self, s, d, k, p, size, ttl):
        self.originate(self.nodes[s], self.nodes[d], k, p, size, ttl)

    def _resolve(self, m: Message) -> None:
        if m.resolved:
            return
        m.resolved = True
        src = self.nodes[m.src]
        if src.alive:
            src.router.on_e2e(m, False, None, self.now)

    # ------------------------------------------------------------- queues
    def _enqueue(self, node: Node, c: Copy) -> bool:
        if len(node.queue) >= node.cap:
            # evict lowest priority (highest number), newest arrival first
            victim = max(node.queue, key=lambda x: (x.msg.priority, x.arrived))
            if victim.msg.priority <= c.msg.priority:
                return False
            node.queue.remove(victim)
            self.stats.evicted += 1
        node.queue.append(c)
        return True

    def wake(self, node: Node, delay: float) -> None:
        t = self.now + delay
        if t < node.next_wake - 1e-9:
            node.next_wake = t
            self.at(t, self._service, node, t)

    def _service(self, node: Node, stamp: float) -> None:
        if stamp != node.next_wake:
            return
        node.next_wake = INF
        if not node.alive:
            return
        now = self.now
        live = []
        for c in node.queue:
            if c.msg.expiry <= now:
                self.stats.expired_copies += 1
            else:
                live.append(c)
        node.queue = live
        if not live:
            return
        live.sort(key=lambda c: (c.msg.priority, c.arrived))
        ifaces = set(l.iface for l in node.links)
        scanned = 0
        earliest = INF
        for c in live:
            if all(node.busy.get(i) for i in ifaces):
                break
            if c.not_before > now:
                earliest = min(earliest, c.not_before)
                continue
            acts = node.router.decide(c, now)
            scanned += 1
            for (link, nbr, tokens) in acts:
                if node.busy.get(link.iface):
                    continue
                self._start_exchange(node, c, link, nbr, tokens)
                break
            if scanned >= self.MAX_SCAN:
                break
        delay = self.POLL
        if earliest < INF:
            delay = min(delay, max(0.05, earliest - now))
        self.wake(node, delay)

    # ------------------------------------------------------------- exchanges
    def _start_exchange(self, node: Node, c: Copy, link: Link, nbr: int, tokens: int) -> None:
        node.busy[link.iface] = True
        n = link.medium.frames_for(c.msg.size)
        ex = {"node": node, "copy": c, "link": link, "nbr": nbr, "tokens": tokens,
              "n": n, "missing": set(range(n)), "round": 0, "t0": self.now, "epoch": node.epoch}
        self.stats.exchanges += 1
        self._round(ex)

    def _tx(self, link: Link, start: float, dur: float, ftime: float) -> Tx:
        tx = Tx(start, start + dur, ftime)
        if link.domain is not None:
            link.domain.register(tx)
        return tx

    def _stale(self, ex: dict) -> bool:
        node = ex["node"]
        if not node.alive or node.epoch != ex["epoch"]:
            if node.epoch == ex["epoch"]:
                node.busy.pop(ex["link"].iface, None)
            return True
        return False

    def _round(self, ex: dict) -> None:
        if self._stale(ex):
            return
        link: Link = ex["link"]
        if link.domain is not None:
            busy = link.domain.busy_until(self.now)
            if busy is not None and self.rng.random() < link.domain.carrier_sense:
                self.at(busy + self.rng.uniform(0.01, 0.2), self._round, ex)
                return
        frames = sorted(ex["missing"])
        ftime = link.frame_time()
        dur = ftime * len(frames)
        tx = self._tx(link, self.now, dur, ftime)
        if link.domain is not None and ex["round"] == 0:
            # TDF frames expose the 8-byte content tag: domain members can overhear forwarding
            uid = ex["copy"].msg.uid
            for mid in link.domain.members:
                if mid != node_id_of(ex) and mid != ex["nbr"]:
                    o = self.nodes[mid]
                    if o.alive and o.router is not None:
                        o.router.on_overhear(node_id_of(ex), uid, self.now)
        self.stats.data_airtime += dur
        self.stats.frames_sent += len(frames)
        ex["round"] += 1
        self.at(self.now + dur, self._round_end, ex, tx, frames)

    def _round_end(self, ex: dict, tx: Tx, frames: list) -> None:
        if self._stale(ex):
            return
        node: Node = ex["node"]
        link: Link = ex["link"]
        rx = self.nodes[ex["nbr"]]
        uid = ex["copy"].msg.uid
        got = []
        if node.alive and rx.alive and link.is_up(tx.start):
            fd = link.first_down(tx.start, tx.end)
            q = link.loss_at(tx.start)
            for k, f in enumerate(frames):
                fs = tx.start + k * tx.frame_time
                fe = fs + tx.frame_time
                if fd is not None and fe > fd:
                    break
                if tx.frame_collided(fs, fe):
                    continue
                if self.rng.random() < q:
                    continue
                got.append(f)
        decision = None
        key = (uid, node.id)
        dec = rx.decisions.get(key)
        if dec is not None and self.now - dec[1] > 300.0:
            rx.decisions.pop(key, None)
            dec = None
        if got or dec is not None:
            part, _ = rx.partials.get(uid, (set(), 0.0))
            part = part | set(got)
            if dec is not None:
                decision = dec[0]
            elif len(part) >= ex["n"]:
                decision = self._custody(rx, node, ex)
                rx.decisions[key] = (decision, self.now)
                rx.partials.pop(uid, None)
            else:
                rx.partials[uid] = (part, self.now)
            have = set(range(ex["n"])) if decision else part
            nb = ack_bytes(ex["n"], node.router.piggyback_bytes)
            ta = link.frame_time(nb)
            atx = self._tx(link, self.now, ta, ta)
            self.stats.ack_airtime += ta
            self.at(self.now + ta, self._ack_end, ex, atx, len(frames), len(got), have, decision)
        else:
            timeout = link.frame_time(ack_bytes(ex["n"], True)) + 0.5
            self.at(self.now + timeout, self._ack_lost, ex, len(frames))

    def _ack_end(self, ex, atx: Tx, sent, got, have, decision) -> None:
        if self._stale(ex):
            return
        node: Node = ex["node"]
        link: Link = ex["link"]
        ok = (link.is_up(atx.start) and link.first_down(atx.start, atx.end) is None
              and not atx.frame_collided(atx.start, atx.end)
              and self.rng.random() >= link.loss_at(atx.start))
        if not ok:
            self._ack_lost(ex, sent)
            return
        node.router.on_round(link, ex["nbr"], sent, got, True, self.now)
        ex["missing"] = set(range(ex["n"])) - have
        if decision is not None:
            self._finish(ex, decision)
        elif ex["round"] < self.ROUNDS_PER_EXCHANGE:
            self._round(ex)
        else:
            self._fail(ex)

    def _ack_lost(self, ex, sent) -> None:
        if self._stale(ex):
            return
        node: Node = ex["node"]
        link: Link = ex["link"]
        node.router.on_round(link, ex["nbr"], sent, 0, False, self.now)
        if ex["round"] < self.ROUNDS_PER_EXCHANGE:
            backoff = self.rng.uniform(0.2, 1.0) * ex["round"]
            self.at(self.now + backoff, self._round, ex)
        else:
            self._fail(ex)

    def _fail(self, ex) -> None:
        node: Node = ex["node"]
        c: Copy = ex["copy"]
        link: Link = ex["link"]
        self.stats.exchange_fail += 1
        c.fails += 1
        c.not_before = self.now + self.rng.uniform(2.0, 6.0) * min(2 ** min(c.fails, 5), 32) / 2
        node.router.on_hop_result(c, link, ex["nbr"], "fail", None, self.now - ex["t0"], self.now)
        node.busy.pop(link.iface, None)
        self.wake(node, 0.0)

    def _finish(self, ex, decision: str) -> None:
        node: Node = ex["node"]
        c: Copy = ex["copy"]
        link: Link = ex["link"]
        nbr = ex["nbr"]
        rx = self.nodes[nbr]
        hop_time = self.now - ex["t0"]
        pig = None
        if decision in ("accept", "dup") and rx.alive and rx.router is not None:
            pig = rx.router.piggyback(c.msg.dst, c.msg.priority, self.now, node.id)
        node.router.on_hop_result(c, link, nbr, decision, pig, hop_time, self.now)
        if decision == "accept":
            self.stats.custody_transfers += 1
            c.tokens -= ex["tokens"]
            c.given.add(nbr)
            if node.router.multi_copy and c.msg.dst != nbr:
                if c.tokens <= 0 and not node.router.keeps_copy:
                    self._drop(node, c)
            elif c.tokens <= 0 or c.msg.dst == nbr:
                self._drop(node, c)
        elif decision == "dup":
            c.given.add(nbr)
            if not node.router.multi_copy or c.msg.dst == nbr:
                self._drop(node, c)
        elif decision == "expired":
            self._drop(node, c)
        else:  # reject (queue full)
            c.fails += 1
            c.not_before = self.now + self.rng.uniform(5.0, 15.0)
        node.busy.pop(link.iface, None)
        self.wake(node, 0.0)

    def _drop(self, node: Node, c: Copy) -> None:
        try:
            node.queue.remove(c)
        except ValueError:
            pass

    # ------------------------------------------------------------- custody
    def _custody(self, rx: Node, tx: Node, ex: dict) -> str:
        c: Copy = ex["copy"]
        m = c.msg
        if m.expiry <= self.now:
            return "expired"
        if rx.id == m.dst:
            if m.delivered_at is not None:
                self.stats.duplicates_at_dst += 1
                return "dup"
            self._deliver(rx, m)
            return "accept"
        if tx.router.multi_copy:
            if m.uid in rx.seen:
                return "dup"
        elif rx.holds(m.uid):
            return "dup"
        if rx.malicious:
            rx.seen.add(m.uid)
            self.stats.blackholed += 1
            return "accept"
        nc = Copy(m, ex["tokens"], (c.path + (rx.id,))[-8:], self.now, c.hops + 1)
        if not self._enqueue(rx, nc):
            return "reject"
        rx.seen.add(m.uid)
        rx.router.on_receive(nc, tx.id, self.now)
        self.wake(rx, 0.0)
        return "accept"

    def _deliver(self, rx: Node, m: Message) -> None:
        if m.delivered_at is None:
            m.delivered_at = self.now
        if m.kind == "receipt":
            orig = self.messages[m.receipt_for]
            if orig.confirmed_at is None and not orig.resolved:
                orig.confirmed_at = self.now
                orig.resolved = True
                rx.router.on_e2e(orig, True, orig.delivered_at - orig.created, self.now)
        else:
            src = self.nodes[m.src]
            self.originate(rx, src, "receipt", P1, 64, ttl=m.expiry - self.now + self.receipt_grace,
                           receipt_for=m.uid)

    # ------------------------------------------------------------- beacons
    def _beacon(self, node: Node) -> None:
        nxt = self.now + self.beacon_period * self.rng.uniform(0.9, 1.1)
        self.at(nxt, self._beacon, node)
        if not node.alive:
            return
        node.beacon_seq += 1
        for iface, links in node.ifaces().items():
            self._send_beacon(node, iface, links, node.beacon_seq, 0)

    def _send_beacon(self, node: Node, iface: str, links: list, seq: int, tries: int) -> None:
        if not node.alive:
            return
        if node.busy.get(iface):
            if tries < 20:
                self.at(self.now + 0.5, self._send_beacon, node, iface, links, seq, tries + 1)
            return
        payload, nbytes = node.router.beacon_payload(self.now)
        # broadcast at the slowest medium of the interface (e.g. highest LoRa SF)
        slow = max(links, key=lambda l: l.frame_time())
        med = slow.medium
        nfr = med.frames_for(nbytes)
        ftime = slow.frame_time()
        dur = nfr * ftime
        dom = slow.domain
        tx = Tx(self.now, self.now + dur, ftime)
        if dom is not None:
            busy = dom.busy_until(self.now)
            if busy is not None and self.rng.random() < dom.carrier_sense and tries < 20:
                self.at(busy + self.rng.uniform(0.01, 0.3), self._send_beacon, node, iface, links, seq, tries + 1)
                return
            dom.register(tx)
        self.stats.ctrl_airtime += dur
        node.busy[iface] = True
        self.at(self.now + dur, self._beacon_end, node, iface, links, tx, payload, seq, nfr)

    def _beacon_end(self, node, iface, links, tx: Tx, payload, seq, nfr) -> None:
        node.busy.pop(iface, None)
        if not node.alive:
            return
        for l in links:
            rx = self.nodes[l.other(node.id)]
            if not rx.alive or not l.is_up(tx.start) or l.first_down(tx.start, tx.end) is not None:
                continue
            if tx.frame_collided(tx.start, tx.end):
                continue
            q = l.loss_at(tx.start)
            if any(self.rng.random() < q for _ in range(nfr)):
                continue
            rx.router.on_beacon(l, node.id, payload, seq, self.now)
            if rx.queue:
                self.wake(rx, 0.0)
        self.wake(node, 0.0)

    # ------------------------------------------------------------- failures
    def crash(self, node: Node, duration: float, router_factory: Callable) -> None:
        def down():
            node.alive = False
            node.epoch += 1
            node.seen.clear()
            node.decisions.clear()
            self.stats.crash_lost += len(node.queue)
            node.queue.clear()
            node.partials.clear()
            node.busy.clear()
        def up():
            node.alive = True
            node.router = router_factory(self, node)
            node.next_wake = INF
        down()
        self.at(self.now + duration, up)

    # ------------------------------------------------------------- metrics
    def summary(self, t_from: float, t_to: float) -> dict:
        ms = [m for m in self.messages.values() if m.kind != "receipt" and t_from <= m.created < t_to]
        out = {"router": self.router_name, "seed": self.seed, "n_msgs": len(ms)}
        def block(sel, tag):
            n = len(sel)
            dl = [m for m in sel if m.delivered_at is not None and m.delivered_at <= m.expiry]
            cf = [m for m in sel if m.confirmed_at is not None]
            lat = sorted(m.delivered_at - m.created for m in dl)
            out[f"{tag}n"] = n
            out[f"{tag}pdr"] = len(dl) / n if n else float("nan")
            out[f"{tag}confirmed"] = len(cf) / n if n else float("nan")
            out[f"{tag}lat_mean"] = sum(lat) / len(lat) if lat else float("nan")
            out[f"{tag}lat_p50"] = lat[len(lat) // 2] if lat else float("nan")
            out[f"{tag}lat_p95"] = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else float("nan")
            return dl
        dl = block(ms, "")
        block([m for m in ms if m.priority == P0], "sos_")
        st = self.stats
        nd = max(1, len(dl))
        out.update({
            "data_air_per_deliv": (st.data_airtime + st.ack_airtime) / nd,
            "ctrl_air_per_deliv": st.ctrl_airtime / nd,
            "total_air_per_deliv": (st.data_airtime + st.ack_airtime + st.ctrl_airtime) / nd,
            "ctrl_airtime": st.ctrl_airtime, "data_airtime": st.data_airtime + st.ack_airtime,
            "exchanges": st.exchanges, "exchange_fail": st.exchange_fail,
            "custody_transfers": st.custody_transfers, "evicted": st.evicted,
            "blackholed": st.blackholed, "crash_lost": st.crash_lost,
            "dup_at_dst": st.duplicates_at_dst,
            "collisions": sum(d.collisions for d in self.domains.values()),
        })
        return out
