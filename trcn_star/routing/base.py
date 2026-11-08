"""Routing-policy interface shared by all baselines and STAR."""
from __future__ import annotations

from ..network import INF


class Router:
    name = "base"
    uses_beacons = True
    multi_copy = False      # replicate (copy stays at sender)
    keeps_copy = False      # multi-copy: sender keeps its copy after a transfer
    piggyback_bytes = False  # ACK carries routing piggyback
    ENTRY_BYTES = 9   # mailbox prefix 4, metric 2, next-hop index 1, seqno 2
    K_ENTRIES = 24
    BEACON_HEADER = 12
    SEQ_EVERY = 4     # beacons between own sequence-number increments

    def __init__(self, sim, node):
        self.sim = sim
        self.node = node
        self.heard: dict[int, float] = {}      # link id -> last beacon time
        self._rot = 0
        self.seq = 0                           # own destination sequence number
        self.fd: dict[int, tuple] = {}         # d -> (seq, metric) feasible distance

    # ----------------------------------------------------------- Babel feasibility (RFC 8966)
    def feasible(self, d: int, seq: int, metric: float) -> bool:
        f = self.fd.get(d)
        if f is None:
            return True
        return seq > f[0] or (seq == f[0] and metric < f[1])

    def note_advertised(self, d: int, seq: int, metric: float) -> None:
        f = self.fd.get(d)
        if f is None or seq > f[0] or (seq == f[0] and metric < f[1]):
            self.fd[d] = (seq, metric)

    # ----------------------------------------------------------- utilities
    def neighbours(self):
        for l in self.node.links:
            yield l, l.other(self.node.id)

    def alive(self, link, now: float, factor: float = 2.5) -> bool:
        t = self.heard.get(link.id)
        return t is not None and now - t <= factor * self.sim.beacon_period

    def rotating(self, keys: list) -> list:
        if len(keys) <= self.K_ENTRIES:
            return keys
        k = self.K_ENTRIES
        start = self._rot % len(keys)
        self._rot += k
        return (keys + keys)[start:start + k]

    def beacon_size(self, n_entries: int) -> int:
        return self.BEACON_HEADER + self.ENTRY_BYTES * n_entries

    # ----------------------------------------------------------- hooks
    def initial_tokens(self, msg) -> int:
        return 1

    def beacon_payload(self, now: float):
        return None, self.BEACON_HEADER

    def on_beacon(self, link, frm: int, payload, seq: int, now: float) -> None:
        self.heard[link.id] = now

    def decide(self, copy, now: float) -> list:
        return []

    def on_round(self, link, nbr: int, sent: int, got: int, acked: bool, now: float) -> None:
        pass

    def on_hop_result(self, copy, link, nbr: int, result: str, piggy, hop_time: float, now: float) -> None:
        pass

    def on_receive(self, copy, frm: int, now: float) -> None:
        pass

    def piggyback(self, dst: int, prio: int, now: float, asker: int = -1):
        return None

    def on_e2e(self, msg, ok: bool, delay, now: float) -> None:
        pass

    def on_overhear(self, sender: int, uid: int, now: float) -> None:
        pass
