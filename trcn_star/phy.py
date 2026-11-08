"""Physical-layer airtime models.

The LoRa time-on-air expression follows the Semtech SX127x/SX126x modem
designer's guide (AN1200.13).  All other media are modelled as a nominal
bit-rate plus a fixed per-frame overhead.  These are *synthetic* models used
for relative comparison of routing policies, not RF measurements.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TDF_HEADER = 24  # TRCN TDFv1 frame header bytes (spec section 4)


def lora_time_on_air(payload_bytes: int, sf: int = 9, bw: float = 125e3, cr: int = 1,
                     preamble: int = 8, explicit_header: bool = True, crc: bool = True,
                     low_dr_opt: bool | None = None) -> float:
    """LoRa packet time on air in seconds (Semtech AN1200.13, eq. for T_packet)."""
    if not 6 <= sf <= 12:
        raise ValueError("spreading factor must be in 6..12")
    if payload_bytes < 0 or payload_bytes > 255:
        raise ValueError("LoRa PHY payload must be 0..255 bytes")
    if low_dr_opt is None:
        low_dr_opt = sf >= 11 and bw <= 125e3
    t_sym = (2 ** sf) / bw
    t_pre = (preamble + 4.25) * t_sym
    h = 0 if explicit_header else 1
    de = 1 if low_dr_opt else 0
    num = 8 * payload_bytes - 4 * sf + 28 + 16 * (1 if crc else 0) - 20 * h
    n_payload = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return t_pre + n_payload * t_sym


@dataclass(frozen=True)
class Medium:
    """A link technology profile."""
    name: str
    mtu: int                 # bytes per TDF frame, header included
    bps: float = 0.0         # nominal bit rate (non-LoRa)
    overhead: float = 0.0    # fixed per-frame overhead seconds (non-LoRa)
    sf: int = 0              # LoRa spreading factor; 0 = not LoRa
    bw: float = 125e3

    @property
    def is_lora(self) -> bool:
        return self.sf > 0

    def frame_time(self, nbytes: int) -> float:
        nbytes = min(nbytes, self.mtu)
        if self.is_lora:
            return lora_time_on_air(nbytes, sf=self.sf, bw=self.bw)
        return nbytes * 8.0 / self.bps + self.overhead

    def frames_for(self, wire_bytes: int) -> int:
        return max(1, math.ceil(wire_bytes / (self.mtu - TDF_HEADER)))

    def full_frame_time(self) -> float:
        return self.frame_time(self.mtu)


def lora(sf: int, mtu: int = 222, name: str | None = None) -> Medium:
    return Medium(name=name or f"lora_sf{sf}", mtu=mtu, sf=sf)


WIFI_AWARE = Medium("wifi_aware", mtu=1024, bps=2e6, overhead=2e-3)
BLE = Medium("ble", mtu=244, bps=2e5, overhead=3e-3)
ETHERNET = Medium("eth", mtu=1400, bps=1e8, overhead=1e-4)
MICROWAVE = Medium("mw", mtu=1024, bps=1e6, overhead=1e-3)

# ACK = 'AK' + message id(16) + HMAC(12) = 30 bytes (spec section 5),
# plus a fragment bitmap and an 8-byte routing piggyback for learning routers.
ACK_BASE = 30
PIGGYBACK = 8


def ack_bytes(n_frames: int, piggyback: bool) -> int:
    return ACK_BASE + math.ceil(n_frames / 8) + (PIGGYBACK if piggyback else 0)
