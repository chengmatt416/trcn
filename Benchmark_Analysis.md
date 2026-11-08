# TRCN v3.2 Synthetic Benchmark Notes

**Do not treat these results as real RF measurements.** All nodes, radios, sender keys, and packet movement are simulated. The radio frame-time model uses `bits/bps`, not true LoRa airtime. Routing policies are preloaded and control packet costs ignored. The single-link channel collision model is intentionally crude.

## Seven-node traffic test

Every run has an identical 7-node topology; the sole cross-region backbone returns at simulated t=80 seconds. Senders queue all messages at t=0; observation window is 600 seconds. New messages use default TTL=900 seconds, so some may remain pending at the observation cutoff.

| Submitted | Reached recipient by 600 s | Originator received authenticated receipt | Queued packets | Mean destination delay (s) |
|---:|---:|---:|---:|---:|
| 1 | 1 | 1 | 0 | 84.058 |
| 5 | 5 | 5 | 0 | 97.858 |
| 10 | 10 | 10 | 0 | 114.858 |
| 20 | 20 | 20 | 0 | 149.708 |
| 40 | 40 | 40 | 0 | 222.508 |
| 80 | 73 | 71 | 9 | 339.77 |

A discrepancy between recipient arrival and sender confirmation is expected: the signed return receipt must travel the reverse low-speed path and may be delayed. At high load, expiry, stale route hints and congestion produce more queued packets. These numbers depend on nominal bitrates, selected MTUs and the simplified contention model and must not be used as nationwide design capacities.

## Channel-collision experiment

10 random seeds, two independent sender/receiver pairs sharing one synthetic low-speed channel. Carrier-sense probability intentionally set to zero to provoke collisions and validate retry behavior. This is a stress fixture, not a real carrier-sense parameter.

| Seed | Delivered by 120 s | Confirmed | Collided transmissions | Retries |
|---:|---:|---:|---:|---:|
| 0 | 2 | 2 | 58 | 10 |
| 1 | 1 | 0 | 74 | 14 |
| 2 | 0 | 0 | 62 | 14 |
| 3 | 2 | 2 | 59 | 12 |
| 4 | 1 | 1 | 74 | 14 |
| 5 | 2 | 2 | 62 | 10 |
| 6 | 2 | 2 | 52 | 8 |
| 7 | 2 | 2 | 59 | 10 |
| 8 | 2 | 2 | 52 | 10 |
| 9 | 2 | 2 | 46 | 10 |

## Observability and definitions

- `delivered_to_recipient`: target device could authenticate and decrypt a complete TME message.
- `authenticated_e2e_receipts`: sender received the intended recipient's separately encrypted/signed receipt.
- `custody_acks`: per-hop acceptance of a complete packet by the next node; **not** delivery.
- `queued_packets`: aggregate unsent or unacknowledged custody copies at the observation cutoff, **not** number of unique lost user messages.
- `collisions`: attempted data/control transmissions that overlapped within an explicitly configured shared channel, not actual radio interference measurements.
- `carrier_sense_deferrals`: synthetic deferrals on observing a channel in use.

## Main limitations to fix next

1. Per-node preloaded next-hop routes; no authenticated or capacity-bounded route advertisements.
2. RAM-only custody; no transactional durable ACK.
3. Whole-message retransmits after missing ACK; no per-fragment selective repeat.
4. Simplified Bernoulli loss/collision; no actual LoRa PHY and no licensed backbone profile.
5. OS and radio firmware behavior not modeled.
