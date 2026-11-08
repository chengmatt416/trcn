# TRCN v3.2 — Threat Model & Security Invariants

**Status:** design review / not a production security assessment.  
**Assets:** physical safety, emergency-message correctness, recipient confidentiality, civilian location privacy, official notice authority, radio/channel availability, store-and-forward custody and service fairness.

## A. Trust boundaries

1. **Official issuer / authorization root:** May issue authentic identity or official-notice roles; not an online dependency for steady-state messaging. A trust root breach is severe; offline users cannot know immediate revocation.
2. **User device:** May be compromised, stolen, spoofed; government VC possession does *not* guarantee every message is truthful.
3. **Phone relay:** Untrusted by default. Must not receive decrypted end-to-end payload or original government credentials.
4. **Shelter gateway:** Stores encrypted traffic and decides local scheduling; a legitimate shelter still can drop traffic, falsify routing information or leak metadata.
5. **Regional backbone gateway:** Critical physical availability; is not inherently an identity authority or single nationwide router.
6. **Radio medium:** Open to passive interception, active jamming, collision and spoofing; CRC does not provide origin authentication.
7. **Simulator orchestrator:** Holds complete topology and simulated event queue for **testing only**. It must not exist as a required service in actual deployments.

## B. Threat register

| ID | Attack / failure | Required mitigation | v3.2 status |
|---|---|---|---|
| T01 | Spoofed identity | Signature bound to public key + offline issuer verification when claiming U1/U2 | Experiment for lab identity and mock issuer only |
| T02 | Fake government notice | Role-specific credential, region bound, signed notice, expiry + sequence anti-replay | Role/scope/expiry/signature tested; seq replay cache still TODO |
| T03 | Forged custody ACK | Authenticated per-link ACK with replay resistant key + counter | Simulated HMAC; **key provision insecure** and no ACK replay counter |
| T04 | Message tampering | AEAD bound to immutable envelope header | Tested in lab |
| T05 | Sybil floods | Per-device/channel rate limits, quotas, optional verified class without excluding anonymous distress | Priorities, caps only; no proper adaptive fairness |
| T06 | Radio jamming/collision | Multiple media, region-level redundancy, queue under partitions | Simple collision-domain model; real RF not tested |
| T07 | Privacy tracking | Rotating pairwise mailbox, short-lived hints, minimal metadata | Derivation function exists, discovery and traffic-analysis mitigation TODO |
| T08 | Compromised phone key | Hardware-backed keys where supported, re-key, remote revocation propagation | No hardware keys or revocation propagation |
| T09 | Stale issuer/role revocation | Signed status snapshot, explicitly show freshness and permission policy | Lab mock only; cannot have fresh revocation with no path |
| T10 | Route poisoning | Authenticated bounded regional adverts, monotonic versions, quotas | **Not implemented** (static local route hints) |
| T11 | Lost custody after power cut | Durable encrypted queue + fsync before acknowledging | **Not implemented**; RAM only |
| T12 | Congestion starving SOS | Auth-aware service classes, bounded retries, protected minimum SOS quota | Coarse requested-priority ordering only |
| T13 | Insecure phone-number directory | No broadcast hashes; mutual prior contact exchange or private discovery | No phone lookup in v3.2 |
| T14 | Key compromise with long-lived E2EE | Audited KEM / ratchet, per-peer rotation | Lab E2EE lacks post-compromise recovery and forward secrecy |
| T15 | RF interference from unlawful deployment | Legal bands/certification/site permission, field RF testing | No RF used or deployed |

## C. Security design decisions

- A government credential conveys **verified properties only**, not that a user cannot lie.
- U0 users can request help; unauthenticated messages are not marked as official.
- Claiming an authorized official role requires separate issuer authorization; verified phone number alone is insufficient.
- No mandatory central signing or server API in the message flow; issuer signatures are verified locally.
- Time is not automatically trustworthy when the phone clock can be changed. v3.2 uses simulator time; secure clock policy is future work.
- CRC in TME/TDF is accidental-error detection. The TDF 8-byte tag is not an adversary-resistant proof of origin.
- End-to-end encrypted receipts protect against forged recipient delivery status when contact keys are already exchanged. The model still does not guarantee that a human read or acted on a message.
- A true nationwide hardware system must be independently penetration-tested and red-teamed, especially with disaster notice and relief traffic.

## D. Key design limitation: active attacks against custody

The test simulator uses a deterministic HMAC key from **public node names** so results reproduce. An attacker who knows this construction can forge lab ACKs; this is explicitly *not* suitable for production. `test_forged_custody_ack_rejected` only demonstrates wrong-tag rejection under an assumed unknown lab key; it does **not** establish actual peer authentication.

For deployment, choose a vetted peer-to-peer handshake (for example authenticated Noise protocol profiles with appropriate key provisioning), per-neighbor key expiration, replay counters and persistent admission policy, and verify the selected design against physical device constraints.

## E. Safety and humanitarian constraints

- Never substitute an unverified research network for 119 / 110 / official safety instructions.
- Official notices in demos must show **模擬公告** and use keys not recognizable as real issuer keys.
- Do not expose shelters' sensitive occupant lists, precise distress locations or bulk contact info to all relays.
- Ensure the least connected citizens, disabled users and people without wallet credentials retain a human-accessible help path.
- If the system cannot deliver a message, show pending/expired, never display a false success indicator.

## F. Security tests present in v3.2

See `tests/test_protocol.py`, `tests/test_identity.py`, and `tests/test_simulation.py`. They cover custody eviction correctness, CRC, out-of-order fragments, resource caps, E2EE sender authenticity and tamper detection, local issuer role/scope/expiry/revocation, offline recovery, expiry, no-credential distress, incorrect receipts, forged ACK tag rejection and scheduled collision retry. They are correctness checks in a lab model, not a security audit or cryptographic formal proof.
