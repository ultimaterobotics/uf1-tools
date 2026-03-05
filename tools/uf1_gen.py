import math
import time
from typing import Iterator
from uf1.uf1 import (
    UF1Header,
    encode_frame,
    BLK_STATUS,
    BLK_EMG_RAW,
    build_status,
    build_emg_raw,
)


def generate_frames(
    device_id: int = 0x12345678,
    sample_rate_hz: float = 1150.0,
    samples_per_frame: int = 8,
    seconds: float = 10.0,
    ampl: float = 800.0,
    freq_hz: float = 2.0,
) -> Iterator[bytes]:
    frames_per_sec = sample_rate_hz / samples_per_frame
    dt = 1.0 / frames_per_sec

    seq = 0
    t_src_sample = 0
    start = time.monotonic()

    while True:
        now = time.monotonic()
        if now - start > seconds:
            break

        samples = []
        for i in range(samples_per_frame):
            t = (t_src_sample + i) / sample_rate_hz
            val = ampl * math.sin(2 * math.pi * freq_hz * t)
            samples.append(int(val))

        status = build_status(
            t_src_sample=t_src_sample,
            sample_rate_hz_x100=int(sample_rate_hz * 100),
            battery_pct=90,
            rssi_dbm=-128,
            mode=0,
            status_flags=0,
        )
        emg = build_emg_raw(samples)

        t_us = UF1Header.now_rx_time_us()
        frame = encode_frame(
            device_id=device_id,
            seq=seq,
            t_us=t_us,
            blocks=[(BLK_STATUS, status), (BLK_EMG_RAW, emg)],
            crc32=False,
        )

        yield frame

        seq += 1
        t_src_sample += samples_per_frame
        time.sleep(dt)
