"""Links, availability processes and shared collision domains."""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass, field

from .phy import Medium

INF = float("inf")


class Link:
    """An undirected link between two nodes.

    Availability follows a two-state continuous-time Markov (on/off) process with
    exponentially distributed up and down periods; in addition the scenario may
    force outages (``forced``) or fail the link permanently (``fail_at``).
    While up, every frame is lost independently with probability ``loss``.
    """

    def __init__(self, lid: int, a: int, b: int, medium: Medium, rng: random.Random, *,
                 loss: float = 0.02, up_mean: float = INF, down_mean: float = 0.0,
                 domain: "Domain | None" = None, label: str = ""):
        self.id = lid
        self.a, self.b = a, b
        self.medium = medium
        self.iface = medium.name if not medium.is_lora else "lora"
        self.loss = loss
        self.up_mean = up_mean
        self.down_mean = down_mean
        self.domain = domain
        self.label = label
        self.rng = rng
        self.forced: list[tuple[float, float]] = []
        self.fail_at = INF
        self.loss_changes: list[tuple[float, float]] = []  # (time, new loss)
        always_up = down_mean <= 0 or up_mean == INF
        self._always_up = always_up
        if always_up:
            self._times = [0.0]
            self._states = [True]
        else:
            p_up = up_mean / (up_mean + down_mean)
            self._times = [0.0]
            self._states = [rng.random() < p_up]

    # ------------------------------------------------------------------ helpers
    def other(self, n: int) -> int:
        return self.b if n == self.a else self.a

    def frame_time(self, nbytes: int | None = None) -> float:
        return self.medium.frame_time(self.medium.mtu if nbytes is None else nbytes)

    def loss_at(self, t: float) -> float:
        q = self.loss
        for tc, qn in self.loss_changes:
            if t >= tc:
                q = qn
        return q

    def _extend(self, t: float) -> None:
        if self._always_up:
            return
        while self._times[-1] <= t:
            s = self._states[-1]
            mean = self.up_mean if s else self.down_mean
            self._times.append(self._times[-1] + self.rng.expovariate(1.0 / mean))
            self._states.append(not s)

    def _markov_up(self, t: float) -> bool:
        if self._always_up:
            return True
        self._extend(t)
        i = bisect_right(self._times, t) - 1
        return self._states[i]

    def is_up(self, t: float) -> bool:
        if t >= self.fail_at:
            return False
        for s, e in self.forced:
            if s <= t < e:
                return False
        return self._markov_up(t)

    def first_down(self, t0: float, t1: float) -> float | None:
        """Earliest time in (t0, t1] at which the link goes down, if any."""
        cands = []
        if t0 < self.fail_at <= t1:
            cands.append(self.fail_at)
        for s, _e in self.forced:
            if t0 < s <= t1:
                cands.append(s)
        if not self._always_up:
            self._extend(t1)
            i = bisect_right(self._times, t0)
            while i < len(self._times) and self._times[i] <= t1:
                if not self._states[i]:
                    cands.append(self._times[i])
                    break
                i += 1
        return min(cands) if cands else None

    def nominal_availability(self) -> float:
        if self._always_up:
            return 1.0
        return self.up_mean / (self.up_mean + self.down_mean)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Link({self.id}:{self.a}-{self.b} {self.medium.name} {self.label})"


@dataclass
class Tx:
    start: float
    end: float
    frame_time: float
    coll: list = field(default_factory=list)

    def frame_collided(self, fs: float, fe: float) -> bool:
        for s, e in self.coll:
            if s < fe and fs < e:
                return True
        return False


class Domain:
    """A shared channel: overlapping transmissions corrupt each other."""

    def __init__(self, name: str, carrier_sense: float = 0.9):
        self.name = name
        self.carrier_sense = carrier_sense
        self.active: list[Tx] = []
        self.collisions = 0
        self.members: set[int] = set()

    def _prune(self, t: float) -> None:
        if self.active:
            self.active = [x for x in self.active if x.end > t]

    def busy_until(self, t: float) -> float | None:
        self._prune(t)
        ends = [x.end for x in self.active if x.start <= t < x.end]
        return max(ends) if ends else None

    def register(self, tx: Tx) -> None:
        self._prune(tx.start)
        for o in self.active:
            s, e = max(o.start, tx.start), min(o.end, tx.end)
            if s < e:
                o.coll.append((s, e))
                tx.coll.append((s, e))
                self.collisions += 1
        self.active.append(tx)
