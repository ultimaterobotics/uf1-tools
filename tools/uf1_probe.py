# Stream-health probe for UF1 UDP traffic.
# Use this to verify continuity, per-type frame rates, and missing/gap behavior.
import socket
import time
from dataclasses import dataclass
from typing import Optional, Dict

from uf1.uf1 import decode_frame, BLK_STATUS, parse_status

BLK_EMG_RAW = 0x01
BLK_IMU_6DOF = 0x03
BLK_MAG_3 = 0x04
BLK_QUAT = 0x05


def mode_name(mode: Optional[int]) -> str:
    if mode == 0:
        return "PhoneOpt"
    if mode == 1:
        return "FullStream"
    if mode is None:
        return "?"
    return str(mode)


@dataclass
class DevStats:
    prev_seq_any: Optional[int] = None
    prev_emg_tsrc: Optional[int] = None

    last_mode: Optional[int] = None
    last_sr_hz: Optional[int] = None
    last_batt: Optional[int] = None

    total_frames: int = 0
    emg_frames: int = 0
    quat_frames: int = 0
    aux_frames: int = 0

    seq_gaps_any: int = 0

    emg_missing_chunks: int = 0
    emg_big_steps: int = 0
    emg_bad_steps: int = 0

    step_sum: int = 0
    step_count: int = 0
    tsrc_first: Optional[int] = None
    tsrc_last: Optional[int] = None

    def reset_window(self):
        self.total_frames = 0
        self.emg_frames = 0
        self.quat_frames = 0
        self.aux_frames = 0

        self.seq_gaps_any = 0

        self.emg_missing_chunks = 0
        self.emg_big_steps = 0
        self.emg_bad_steps = 0

        self.step_sum = 0
        self.step_count = 0
        self.tsrc_first = None
        self.tsrc_last = None


def print_window(devices: Dict[int, DevStats], elapsed: float):
    printed = False

    for dev_id in sorted(devices.keys()):
        ds = devices[dev_id]
        if ds.total_frames == 0:
            continue

        total_fps = ds.total_frames / elapsed
        emg_fps = ds.emg_frames / elapsed
        quat_fps = ds.quat_frames / elapsed
        aux_fps = ds.aux_frames / elapsed

        if ds.tsrc_first is not None and ds.tsrc_last is not None:
            implied_sps = ((ds.tsrc_last - ds.tsrc_first) & 0xFFFFFFFF) / elapsed
        else:
            implied_sps = 0.0

        avg_step = (ds.step_sum / ds.step_count) if ds.step_count else 0.0

        batt_str = "??"
        if ds.last_batt is not None and ds.last_batt != 255:
            batt_str = str(ds.last_batt)

        sr_val = ds.last_sr_hz if ds.last_sr_hz is not None else 0

        print(
            f"dev=0x{dev_id:08X} "
            f"fps_total={total_fps:.1f} "
            f"emg_fps={emg_fps:.1f} "
            f"quat_fps={quat_fps:.1f} "
            f"aux_fps={aux_fps:.1f} "
            f"avg_tsrc_step={avg_step:.2f} "
            f"implied_sps={implied_sps:.1f} "
            f"seq_gaps_all={ds.seq_gaps_any} "
            f"emg_missing_chunks={ds.emg_missing_chunks} "
            f"big_tsrc_steps={ds.emg_big_steps} "
            f"bad_tsrc_steps={ds.emg_bad_steps} "
            f"batt={batt_str} "
            f"mode={mode_name(ds.last_mode)} "
            f"sr={sr_val:.1f}Hz"
        )
        printed = True
        ds.reset_window()

    if printed:
        print()


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 26750))
sock.settimeout(0.2)

print("Listening on UDP 26750...")

devices: Dict[int, DevStats] = {}
win_start = time.time()

while True:
    try:
        data, addr = sock.recvfrom(4096)
    except socket.timeout:
        data = None

    now = time.time()
    if now - win_start >= 1.0:
        print_window(devices, now - win_start)
        win_start = now

    if data is None:
        continue

    try:
        hdr, blocks, _ = decode_frame(data)
    except Exception:
        continue

    dev_id = hdr.device_id
    ds = devices.setdefault(dev_id, DevStats())

    st = None
    has_emg = False
    has_quat = False
    has_aux = False

    for t, v in blocks:
        if t == BLK_STATUS:
            st = parse_status(v)
        elif t == BLK_EMG_RAW:
            has_emg = True
        elif t == BLK_QUAT:
            has_quat = True
        elif t == BLK_IMU_6DOF or t == BLK_MAG_3:
            has_aux = True

    ds.total_frames += 1

    if ds.prev_seq_any is not None:
        seq_step = (hdr.seq - ds.prev_seq_any) & 0xFFFFFFFF
        if seq_step > 1:
            ds.seq_gaps_any += seq_step - 1
    ds.prev_seq_any = hdr.seq

    if st is not None:
        mode = st.get("mode")
        sr = st.get("sample_rate_hz")
        batt = st.get("battery_pct")

        if mode is not None:
            ds.last_mode = mode

        if sr is not None and sr != 0:
            ds.last_sr_hz = sr

        if batt is not None and batt != 255:
            ds.last_batt = batt

    if has_emg:
        ds.emg_frames += 1

        if st is not None:
            tsrc = st.get("t_src_sample")
            if tsrc is not None:
                if ds.tsrc_first is None:
                    ds.tsrc_first = tsrc
                ds.tsrc_last = tsrc

                if ds.prev_emg_tsrc is not None:
                    step = (tsrc - ds.prev_emg_tsrc) & 0xFFFFFFFF
                    ds.step_sum += step
                    ds.step_count += 1

                    if step > 8:
                        ds.emg_big_steps += 1
                        ds.emg_missing_chunks += max(0, step // 8 - 1)

                    if step != 8 and (step % 8) != 0:
                        ds.emg_bad_steps += 1

                ds.prev_emg_tsrc = tsrc

    if has_quat:
        ds.quat_frames += 1

    if has_aux:
        ds.aux_frames += 1
