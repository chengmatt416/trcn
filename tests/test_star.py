"""Correctness tests for the PHY model, simulator invariants and STAR learning."""
import math
import random

import pytest

from trcn_star.network import Domain, Link, Tx
from trcn_star.phy import WIFI_AWARE, lora, lora_time_on_air
from trcn_star.routing.star import LinkPosterior, StarRouter, phi
from trcn_star.scenarios import disaster, make_factory, seven_node
from trcn_star.sim import P2, Simulator


# ---------------------------------------------------------------- PHY
def test_lora_time_on_air_reference_values():
    # SF7/125 kHz/CR4-5, 20-byte payload, explicit header, CRC: 56.6 ms (Semtech calculator)
    assert lora_time_on_air(20, sf=7) == pytest.approx(0.05658, abs=2e-4)
    # monotone in payload and spreading factor
    assert lora_time_on_air(100, sf=9) > lora_time_on_air(50, sf=9)
    assert lora_time_on_air(50, sf=10) > lora_time_on_air(50, sf=9)
    # low-data-rate optimisation switches on at SF11/125 kHz
    assert lora_time_on_air(50, sf=11) > 1.5 * lora_time_on_air(50, sf=10)


def test_lora_payload_bounds():
    with pytest.raises(ValueError):
        lora_time_on_air(300, sf=9)
    with pytest.raises(ValueError):
        lora_time_on_air(10, sf=13)


def test_fragment_count():
    m = lora(9)          # MTU 222 -> 198 bytes of TME data per frame
    assert m.frames_for(198) == 1
    assert m.frames_for(199) == 2


# ---------------------------------------------------------------- links/domains
def test_markov_link_availability_matches_stationary_fraction():
    l = Link(0, 0, 1, WIFI_AWARE, random.Random(3), up_mean=100.0, down_mean=50.0)
    ups = sum(l.is_up(t) for t in range(0, 200000, 7))
    assert ups / len(range(0, 200000, 7)) == pytest.approx(2 / 3, abs=0.03)


def test_forced_outage_and_first_down():
    l = Link(0, 0, 1, WIFI_AWARE, random.Random(0))
    l.forced.append((10.0, 20.0))
    assert l.is_up(5.0) and not l.is_up(15.0) and l.is_up(25.0)
    assert l.first_down(5.0, 12.0) == 10.0
    l.fail_at = 30.0
    assert not l.is_up(31.0)


def test_domain_collisions_mark_both_transmissions():
    d = Domain("x", carrier_sense=0.0)
    a, b = Tx(0.0, 1.0, 0.25), Tx(0.5, 1.5, 0.25)
    d.register(a)
    d.register(b)
    assert a.frame_collided(0.5, 0.75) and b.frame_collided(0.5, 0.75)
    assert not a.frame_collided(0.0, 0.25)


# ---------------------------------------------------------------- posterior
def test_posterior_learns_and_forgets():
    p = LinkPosterior(hl_f=100.0)
    for t in range(10):
        p.frames(2, 10, float(t))
    assert p.f_mean() < 0.35
    assert p.f_lcb() < p.f_mean()
    p.frames(0, 0, 2000.0)               # long silence: decays back to the prior
    assert p.f_mean() > 0.75


def test_phi():
    assert phi(0.0) == pytest.approx(0.5)
    assert phi(1.96) == pytest.approx(0.975, abs=1e-3)


# ---------------------------------------------------------------- simulator invariants
def test_seven_node_single_message_delivered_after_backbone_returns():
    sim, s = seven_node(0, "Static", 1)
    assert s["pdr"] == 1.0 and s["confirmed"] == 1.0
    assert s["lat_mean"] >= 80.0        # cannot cross before the backbone returns


@pytest.mark.parametrize("router", ["STAR", "ETX-DV", "PRoPHET"])
def test_no_double_counting_and_ratios_bounded(router):
    out = disaster(101, router, duration=900.0)
    assert 0.0 <= out["pdr"] <= 1.0
    assert out["confirmed"] <= out["pdr"] + 1e-9   # cannot confirm undelivered messages


def test_receipt_never_precedes_delivery():
    sim, out, _ = disaster(102, "STAR", duration=900.0, return_sim=True)
    for m in sim.messages.values():
        if m.confirmed_at is not None:
            assert m.delivered_at is not None and m.confirmed_at >= m.delivered_at


def test_single_copy_routers_never_duplicate_custody():
    sim, out, _ = disaster(103, "ETX-DV", duration=900.0, return_sim=True)
    for uid in list(sim.messages)[:200]:
        holders = [n for n in sim.nodes if any(c.msg.uid == uid for c in n.queue)]
        assert len(holders) <= 2      # at most one in-flight duplicate after a lost ACK


# ---------------------------------------------------------------- STAR behaviour
def _two_route_sim(seed):
    """S --(stable, slow LoRa)--> R1 --> D  and  S --(fast, flaky Wi-Fi)--> R2 --> D."""
    sim = Simulator(seed, beacon_period=30.0)
    s = sim.add_node("phone", "S", 64)
    r1 = sim.add_node("shelter", "R1", 256)
    r2 = sim.add_node("shelter", "R2", 256)
    d = sim.add_node("coord", "D", 256)
    sim.add_link(s, r1, lora(9), loss=0.02)
    sim.add_link(r1, d, lora(9), loss=0.02)
    sim.add_link(s, r2, WIFI_AWARE, loss=0.02, up_mean=60.0, down_mean=400.0)
    sim.add_link(r2, d, WIFI_AWARE, loss=0.02)
    return sim, s, r1, r2, d


def test_star_prefers_stable_route_over_flaky_shortcut():
    picks = {"R1": 0, "R2": 0}
    for seed in range(4):
        sim, s, r1, r2, d = _two_route_sim(seed)
        sim.install_routers(make_factory(sim, "STAR"))
        ev = [(600.0 + 60.0 * k, s.id, d.id, "private", P2, 200, 300.0) for k in range(30)]
        sim.schedule_traffic(ev)
        sim.run(4000.0)
        for m in sim.messages.values():
            if m.kind != "receipt" and m.first_hop is not None:
                picks[sim.nodes[m.first_hop].name] += 1
    assert picks["R1"] > picks["R2"]


def test_star_trust_excludes_blackhole():
    out_honest = disaster(104, "STAR", duration=1800.0)
    out_attack = disaster(104, "STAR", duration=1800.0, blackholes=1)
    out_attack_nodef = disaster(104, "STAR-noE2E", duration=1800.0, blackholes=1)
    assert out_attack["pdr"] >= out_attack_nodef["pdr"]
    assert out_honest["pdr"] >= out_attack["pdr"]
