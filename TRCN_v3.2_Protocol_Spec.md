# TRCN v3.2 — Protocol & System Architecture Specification

**Date:** 2026-11-08  
**Status:** Experimental specification; no official Taiwanese government affiliation  
**Constraints:** Hardware budget NT$0; simulated Android and iPhone; no cellular/core server; preissued or lab offline identity evidence; no real emergency radio transmission.

## 1. System invariant

TRCN transports short encrypted asynchronous messages when **at least one spatial or temporal communication path exists**, without requiring a central identity lookup, routing oracle or operator SMSC during steady-state communication. This is a goal, **not a guarantee of reachability**. A total physical partition cannot provide instantaneous delivery.

Trust roots may be previously issued by authorized organizations. Online verification and fresh revocation are not assumed during a disconnected event. No device lacking credentials may be barred from basic distress communication solely for that reason.

## 2. Layer boundaries

| Layer | Core duties | v3.2 state |
|---|---|---|
| User application | Text, SOS, contact sharing, notice UX | Fake test strings only |
| TIC identity | Offline issuer chain, holder binding, role/scope, status | Lab mock, not a VC wallet |
| TME messaging | Immutable end-to-end envelope, E2EE, final receipt | Binary encoding + experimental authenticated encryption |
| TADP directory | Rotating mailbox, local announcements, regional summaries | Pairwise ID derivation only; manual hints |
| TAR routing | Per-node next-hop selection, queue/custody/scheduling | Manual local route hints, store-forward, P0-P3 scheduling |
| TDF transport | MTU-specific frames, CRC, reassembly, custody | Actual binary frames and simplified MAC |
| PHY adapters | Android Wi-Fi Aware / BLE; LoRa; legal long-distance backbone | Simulated links only |

### 2.1 Why not SMS in the network core?

Carrier IP SMS/VoWiFi still depends on available telecom infrastructure and core services, and cannot be treated as a universal offline mesh API. An operator integration may be a *bridge*, but TME carries native TRCN messages even when SMSC/IMS are absent. The mobile UI can be SMS-like, not a claim that iOS/Android native Messages has been replaced.

### 2.2 Avoid duplicate routing layers

Meshtastic and Reticulum are benchmark/interoperability candidates. v3.2 does not encapsulate both stacks on every path. BPv7 provides a reference for delay-tolerant messaging; the proposed TME/TDF are **not RFC 9171 or BPSec compliant**.

## 3. TMEv1 binary envelope

All integers are unsigned, network byte order (`big endian`). This version is a **lab-specific preliminary wire format**, without namespace registration, congestion negotiation, downgrade protection or formal specification review.

| Field | Bytes | Semantics |
|---|---:|---|
| Magic | 2 | ASCII `TR` |
| Version | 1 | 1 |
| Type | 1 | PRIVATE=1, DISTRESS=2, NOTICE=3, RECEIPT=4, SUPPLY=5 |
| Requested priority | 1 | 0=highest, 3=lowest; receiver must not blindly trust |
| Flags | 1 | Bit 0: E2EE payload; all unknown bits rejected |
| Message ID | 16 | Random identity for deduplication |
| Destination mailbox | 16 | Opaque 128-bit mailbox, **not a phone number** |
| Created | 4 | Epoch seconds; actual clock-quality policy unimplemented |
| TTL | 4 | Lifetime in seconds |
| Payload length | 2 | Up to 16 KiB by TRCN profile |
| Encrypted payload | variable | Lab E2EE payload; never plaintext in test fixture |
| CRC32 | 4 | Detect accidental corruption, not malicious forgery |

**Fixed header: 48 bytes; whole-envelope overhead before payload: 52 bytes.** In the lab, encryption adds an ephemeral key, nonce, AEAD authentication tag, sender signing/encryption keys, return mailbox and signature. This is intentionally larger than a production-optimized low-bandwidth envelope.

**Authentication:** Destination and immutable headers are bound to the demonstration AEAD associated data and sender signature. The receiver first authenticates/decrypts before accepting content as coming from the sender. Relays see mailbox, class, lifetime and packet size; they do not see the sender signing key in the encrypted body. Metadata confidentiality is **not** fully provided.

## 4. TDFv1 fragmentation

Frame layout: magic(2), version(1), flags(1), content-hash tag(8), fragment index(2), fragment count(2), whole-message length(4), CRC32(4), payload (MTU minus 24). All fragments of a packet share the 8-byte tag derived from the assembled wire bytes.

- Validate version/flags, lengths, index/count, header and frame checksum before memory allocation.
- Reject partials exceeding 20 KiB per envelope, 32 concurrent partial assemblies per receiver and 256 KiB accounting cap.
- Expire incomplete assemblies after 120 seconds in the lab model.
- Reorder by fragment index; tolerate identical duplicates; reject conflicting duplicates.
- Check assembled length and 8-byte tag; then decode TME and CRC.
- **Tag/CRC are not cryptographic authentication.** End-to-end payload authentication occurs at final recipient.
- Real radio integration needs link-layer admission control, authenticated peer negotiation and explicit FEC/fragment retransmission policies.

**Example:** With MTU 96 bytes, each frame carries at most 72 bytes TME wire data; total frames `ceil(TME_wire_bytes/72)`.

## 5. Store-and-forward / custody

Each node keeps a bounded in-memory queue. A relay only emits a *custody ACK* after complete TDF reassembly, valid TME checksum, TTL check and acceptance into its local queue. Link ACK frames are `AK + original message ID (16) + HMAC (12)` = 30 bytes.

- **Custody ACK != destination delivery.** It confirms the next node assumed responsibility, but the v3.2 simulator stores the packet only in RAM.
- The HMAC depends on a **lab-only link key deterministically derived from node names**. This is absolutely **not secure** for real RF. Production needs authenticated key establishment, anti-replay sequence numbers and persistent storage before ACK.
- The originator records `delivered` only after the recipient sends an encrypted signed RECEIPT envelope and the originator checks the signer's public key against the saved expected recipient.
- Retransmit a complete message on missing custody ACK after bounded wait and jittered backoff; a later version should use selective fragment retry to reduce airtime.
- Source/relay evict lower-priority queues under caps; P0 demand cannot be treated as automatically authenticated truth. Anonymous distress remains possible but rate-limited.
- Per-node concurrency is capped to one in-flight packet in the simulator. This is intentionally conservative rather than an implementation of a full MAC scheduling protocol.
- The simulator accepts local/manual routing hints; **distributed gossip, route authentication, automatic mobility discovery, and path convergence are not implemented**.

## 6. Addressing & identity

- A *device signing key* authenticates its messages; an *encryption key* encrypts to that device. These are different cryptographic functions.
- In production, public telephone numbers must not be broadcast or used as short hashes in routing tables. Identity proof (government-issued or approved VC) is optional; a local lab identity exists without any VC.
- `derive_pairwise_mailbox(secret, epoch, direction)` yields a stable-for-epoch, contact-specific 128-bit address. This function is implemented and tested, but all simulation route tables still use deterministic **lab name-derived** mailboxes for ease of replay.
- The identity proof does not automatically bind a public phone number to a wallet or tell the network where its holder currently is. That mapping requires user authorization and verified contact exchange.
- New contacts known only by phone number cannot be guaranteed discoverable offline without a pre-existing directory or exchange.
- **No global DHT flooded over LoRa.** Future routing uses bounded region-specific encrypted/mailbox hints, TTL and opt-in relay policies.

## 7. Government notices (LAB ONLY)

The mock issuer signs a certificate authorizing `GOV_NOTICE` for a particular region; the authority's key signs `region, issued, expiry, sequence, text`. The offline verifier confirms issuer signature, role, region, expiry, known revocation and the notice signature. No government-issued credential, real phone verification, official public key or certified wallet API is integrated. Sequence anti-replay caching is not implemented yet; published notice sequence should be monotonic per issuing authority.

Do not deploy mock notices as real warnings. Test UI must label them **模擬公告**.

## 8. Synthetic airtime and collisions

- Airtime = frame length * 8 / nominal link bps; all header bytes counted.
- Individual frame loss is an independent Bernoulli experiment. This model ignores actual LoRa spreading factor, coding rates, preambles, duty restrictions, RF path loss and interference geography.
- Frames from different links on the same *declared collision domain* collide if overlapping, except some transmissions sense an ongoing channel and defer. The CSMA probability is a parameter, **not a verified property of a radio product**.
- ACK frames consume airtime. Control-plane manual route hints do **not** consume airtime in v3.2.
- Link status can change at scheduled timestamps; in-flight frames on an invalid link are not delivered.
- Battery drain, radio capture effects, near-far problems, true hidden nodes, GPS/clock synchronization and mobile OS suspension are not implemented.

## 9. Platform implementation contracts

`trcn/ports.py` defines four ports for future adapters: `TransportAdapter`, `CredentialVerifier`, `CustodyStore`, `MailboxDirectory`.

- Android: Kotlin adapter using available Wi-Fi Aware, Bluetooth, connectivity and foreground-service APIs, with explicit user permission and capability checks.
- iOS: Swift adapter based on current platform peer-connectivity APIs and permitted background windows; cannot promise always-on public relay.
- Rust shared core is a future engineering *proposal*, not shipped in this Python package.
- Radio and regional gateway: independent transport implementations after regulatory permission, real radio tests and link-safety review.
- Persist custody records transactionally **before** ACK; encrypt local storage; quota by message class and link admission policy.

## 10. Verification milestones

1. **v3.2 achieved:** binary framing, complete-packet retries, end-to-end encrypted receipt, mock signed roles, loss/collision stress in simplified model.
2. **v3.3 target:** persistent custody and power-failure crash recovery, selective-repeat ARQ, independently authenticated link keys, replay-resistant MAC controls.
3. **v3.4 target:** private mailbox advertisement with decentralized discovery, route expiration and signed route hints; quantify control-plane bandwidth.
4. **v3.5 target:** actual LoRa PHY airtime and Taiwan terrain-aware link simulations; formal privacy testing and network capacity analysis.
5. **Integration later:** official wallet sandbox, real Android/iOS device compatibility checks, permissions, legally authorized RF prototypes.

## 11. Standards and official reference links

- IETF RFC 9171 — <https://www.rfc-editor.org/rfc/rfc9171.html>
- IETF RFC 9172 — <https://www.rfc-editor.org/rfc/rfc9172.html>
- Taiwan Digital Identity Wallet — <https://wallet.gov.tw/zh-tw>
- Taiwan Wallet verifier sandbox — <https://verifier-sandbox.wallet.gov.tw/>
- NCC LP0002 — <https://www.ncc.gov.tw/chinese/files/24020/538_49880_240206_3.pdf>
