from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import struct
import time
import zlib

MAGIC = b"UD"
VERSION = 1
HDR_LEN = 24

# Flags
TIME_SRC_PRESENT = 1 << 0
TIME_US_IS_RX = 1 << 1
CRC32_PRESENT = 1 << 2

# Block types
BLK_EMG_RAW = 0x01
BLK_EMG_FFT4 = 0x02
BLK_IMU_6DOF = 0x03
BLK_MAG_3 = 0x04
BLK_QUAT = 0x05
BLK_STATUS = 0x06
BLK_DEVICE_NAME = 0x07


@dataclass
class UF1Header:
    version: int
    frame_len: int
    flags: int
    device_id: int
    stream_id: int
    seq: int
    t_us: int  # u48

    @staticmethod
    def now_rx_time_us() -> int:
        # Monotonic-ish microseconds on Python (sufficient for tools)
        # For Android you’ll use elapsedRealtimeNanos / monotonic clocks.
        return int(time.monotonic() * 1_000_000)


def pack_u48_le(x: int) -> bytes:
    if x < 0 or x >= (1 << 48):
        raise ValueError("u48 out of range")
    return x.to_bytes(6, "little", signed=False)


def unpack_u48_le(b: bytes) -> int:
    if len(b) != 6:
        raise ValueError("u48 needs 6 bytes")
    return int.from_bytes(b, "little", signed=False)


def encode_tlv(block_type: int, value: bytes) -> bytes:
    if block_type < 0 or block_type > 255:
        raise ValueError("block_type out of range")
    if len(value) > 0xFFFF:
        raise ValueError("TLV too long")
    return struct.pack("<BH", block_type, len(value)) + value


def decode_tlvs(payload: bytes) -> List[Tuple[int, bytes]]:
    out: List[Tuple[int, bytes]] = []
    i = 0
    n = len(payload)
    while i < n:
        if i + 3 > n:
            raise ValueError("Truncated TLV header")
        block_type, blen = struct.unpack_from("<BH", payload, i)
        i += 3
        if i + blen > n:
            raise ValueError("Truncated TLV value")
        out.append((block_type, payload[i : i + blen]))
        i += blen
    return out


def build_status(
    t_src_sample: int,
    sample_rate_hz: int = 0,
    battery_pct: int = 255,
    rssi_dbm: int = -128,
    mode: int = 0,
    status_flags: int = 0,
) -> bytes:
    # len = 10
    return struct.pack(
        "<IHBbBB",
        t_src_sample & 0xFFFFFFFF,
        sample_rate_hz & 0xFFFF,
        battery_pct & 0xFF,
        int(rssi_dbm),
        mode & 0xFF,
        status_flags & 0xFF,
    )


def parse_status(v: bytes) -> Dict[str, int]:
    if len(v) != 10:
        raise ValueError("STATUS len must be 10")
    t_src_sample, sr, batt, rssi, mode, sflags = struct.unpack("<IHBbBB", v)
    return {
        "t_src_sample": t_src_sample,
        "sample_rate_hz": sr,
        "battery_pct": batt,
        "rssi_dbm": rssi,
        "mode": mode,
        "status_flags": sflags,
    }


def build_emg_raw(samples_i16: List[int], channel_count: int = 1) -> bytes:
    # v1: channel_count=1; sample_format=1 (int16)
    if channel_count != 1:
        raise ValueError("v1 supports channel_count=1 only for now")
    if len(samples_i16) > 255:
        raise ValueError("too many samples")
    hdr = struct.pack("<BBBB", channel_count, len(samples_i16), 1, 0)
    body = struct.pack("<" + "h" * len(samples_i16), *samples_i16)
    return hdr + body


def parse_device_name(v: bytes) -> Optional[str]:
    if len(v) < 1 or len(v) > 32:
        return None
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_emg_raw(v: bytes) -> Dict[str, object]:
    if len(v) < 4:
        raise ValueError("EMG_RAW too short")
    ch, n_samp, fmt, _ = struct.unpack_from("<BBBB", v, 0)
    if fmt != 1:
        raise ValueError("Unsupported EMG_RAW format")
    expected = 4 + (ch * n_samp * 2)
    if len(v) != expected:
        raise ValueError(f"EMG_RAW len mismatch: got {len(v)} expected {expected}")
    samples = list(struct.unpack_from("<" + "h" * (ch * n_samp), v, 4))
    return {"channel_count": ch, "samples_per_ch": n_samp, "samples_i16": samples}


def encode_frame(
    device_id: int,
    seq: int,
    t_us: int,
    blocks: List[Tuple[int, bytes]],
    stream_id: int = 0,
    crc32: bool = False,
    flags: Optional[int] = None,
) -> bytes:
    if flags is None:
        flags = TIME_US_IS_RX
        # If STATUS present, we consider TIME_SRC_PRESENT on if caller sets it in flags later;
        # simplest: detect STATUS block existence and set it.    
        if any(bt == BLK_STATUS for bt, _ in blocks):
            flags |= TIME_SRC_PRESENT
    if crc32:
        flags |= CRC32_PRESENT

    payload = b"".join(encode_tlv(bt, val) for bt, val in blocks)
    frame_len = HDR_LEN + len(payload) + (4 if crc32 else 0)

    header = bytearray()
    header += MAGIC
    header += struct.pack("<B", VERSION)
    header += struct.pack("<B", HDR_LEN)
    header += struct.pack("<H", frame_len)
    header += struct.pack("<H", flags)
    header += struct.pack("<I", device_id & 0xFFFFFFFF)
    header += struct.pack("<B", stream_id & 0xFF)
    header += struct.pack("<B", 0)  # reserved
    header += struct.pack("<I", seq & 0xFFFFFFFF)
    header += pack_u48_le(t_us)

    frame = bytes(header) + payload

    if crc32:
        crc = zlib.crc32(frame) & 0xFFFFFFFF
        frame += struct.pack("<I", crc)

    return frame


def decode_frame(
    frame: bytes,
) -> Tuple[UF1Header, List[Tuple[int, bytes]], Optional[int]]:
    if len(frame) < HDR_LEN:
        raise ValueError("Frame too short")

    if frame[0:2] != MAGIC:
        raise ValueError("Bad magic")
    ver = frame[2]
    if ver != VERSION:
        raise ValueError(f"Unsupported version {ver}")
    hdr_len = frame[3]
    if hdr_len != HDR_LEN:
        raise ValueError(f"Unexpected hdr_len {hdr_len}")

    frame_len, flags = struct.unpack_from("<HH", frame, 4)
    if frame_len != len(frame):
        raise ValueError(f"frame_len mismatch: header {frame_len} actual {len(frame)}")

    device_id = struct.unpack_from("<I", frame, 8)[0]
    stream_id = frame[12]
    seq = struct.unpack_from("<I", frame, 14)[0]
    t_us = unpack_u48_le(frame[18:24])

    crc_expected = None
    payload_end = frame_len
    if flags & CRC32_PRESENT:
        if frame_len < HDR_LEN + 4:
            raise ValueError("CRC flag set but frame too short")
        payload_end = frame_len - 4
        crc_expected = struct.unpack_from("<I", frame, payload_end)[0]
        crc_calc = zlib.crc32(frame[:payload_end]) & 0xFFFFFFFF
        if crc_calc != crc_expected:
            raise ValueError(f"CRC mismatch: calc={crc_calc:#x} exp={crc_expected:#x}")

    payload = frame[HDR_LEN:payload_end]
    blocks = decode_tlvs(payload)

    hdr = UF1Header(
        version=ver,
        frame_len=frame_len,
        flags=flags,
        device_id=device_id,
        stream_id=stream_id,
        seq=seq,
        t_us=t_us,
    )
    return hdr, blocks, crc_expected
