# TRCN — Implementation Roadmap, Costs and Decision Log

## 1. Project constraints and scope

- **Hardware cash budget at current stage: NT$0.** Use only a local Python simulator and existing development equipment.
- The user has access to Android and iPhone, but this release has no mobile app or OS-level integration. Treat them as *future* client targets.
- **No physical test area** has been selected; topology is a hypothetical 7-node path. Future GIS studies can evaluate Taoyuan or another region after project selection.
- No real RF operation, no emergency notifications, no citizen data collection, no actual government credentials.

## 2. Execution stages

| Stage | Deliverable | Exit criterion | Outside dependency |
|---|---|---|---|
| v3.2 (current) | binary frames, store-forward, lab crypto, role proof, ACK, toy collision and queue | automated tests + benchmark, source and documented constraints | none |
| v3.3 | durable encrypted custody, process crash and recovery, fragment selective ACK, replay counters | power-loss fault injection yields no falsely acknowledged packet | local disk tests |
| v3.4 | private mailbox adverts, region TTL gossip, mobility, multipath routing | node locality only; no omniscient route; control overhead accounted | none in simulator |
| v3.5 | LoRa air-time PHY approximation, adversarial topology, graph generator, link budget | high-load capacity vs goodput and SOS latency charts | use public standards and map datasets |
| v3.6 | real wallet verifier sandbox, credential type applicability and authority policy | sandbox verifiable credentials verified; do not claim gov authorization | wallet sandbox access |
| v4.0 | Android and iOS adapters with permission/capability matrix | cross-phone direct messages, screen/background limitations documented | actual compatible devices |
| pilot | legal RF equipment and authorized sites, emergency-service governance | monitored reliability, permits, consent and clear user guidance | public partners, budget and review |

## 3. Full cost structure and uncertainties

### Now: simulation-only

Hardware procurement: **NT$0**. Simulator dependencies (Python, `cryptography`, `pytest`) are available without proprietary API subscription; building and testing still require developer time and a computer.

### Future build-out: avoid false precision

A nationwide budget cannot be validated by radio board prices alone. Estimators must separately cover:

1. Shelter endpoint unit materials and backup power.
2. Regional radio links / legally permitted bandwidth.
3. Elevated repeaters, site rental, towers, lightning/weather protection.
4. Commissioning, legal fees, equipment approval, engineering field tests.
5. Reliability monitoring, on-call maintenance, spares, battery replacement.
6. Security testing, signed credential governance, digital wallet integration.
7. Emergency exercises, inclusive citizen-facing UX and ongoing support.

A detailed credible capex estimate requires verified shelter inventory, topology placement, line-of-sight paths, propagation assumptions, spectrum constraints, expected message volume and availability SLOs. The earlier rough 5,928-shelter count should not be treated as a complete RF topology. No budget amount in this repository should be represented as a procurement quote.

## 4. Technical decisions to keep consistent

- **No central SMSC requirement**: regular operator IP SMS is an optional external bridge, not the offline data plane.
- **Government wallet as optional proof source**: official verified VC claims, when appropriately supported, are not required for U0 anonymous distress.
- **No global phone-number-to-peer map**: phone numbers can serve as user-visible aliases only after verified private contact exchange.
- **Store and forward**: no immediate delivery guarantee across partitions.
- **Regional backbone may be a heterogeneous high-capacity radio network**; physical medium determined after legal/terrain evaluation.
- **Separate official authority from ordinary identity**: govt notices have their own scope/key roles.

## 5. Competition research evidence still needed

- Disaster-communications needs assessment with affected school/community populations, not solely a speculative engineering design.
- Survey / interview evidence of current shelter-to-shelter communication gaps and operational constraints.
- Baselines against existing solutions (including Reticulum and Meshtastic where technically relevant).
- Demonstrated throughput, latency and queue management under quantified workloads.
- Credible public-sector integration plan and constraints for spectrum, security and service procurement.
- Accessible fallback paths for citizens without compatible phones, battery or credentials.

## 6. Proposed high-impact next v3.3 task

Implement durable store-and-forward that writes payloads transactionally before hop acknowledgment; build crash/restart tests. A falsely positive ACK could cause silent loss of a distress message during a power cut. This safety issue should be resolved before adding interactive UI polish.
