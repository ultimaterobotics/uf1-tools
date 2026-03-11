import socket
import time
from uf1.uf1 import decode_frame, BLK_STATUS, parse_status

BLK_EMG_RAW = 0x01

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 26750))
sock.settimeout(1.0)

print("Listening on UDP 26750...")

prev_tsrc = None
prev_seq = None

win_start = time.time()
win_frames = 0
win_tsrc_first = None
win_tsrc_last = None
win_seq_gaps = 0
win_tsrc_big_steps = 0
win_step_sum = 0
win_step_count = 0

while True:
    try:
        data, addr = sock.recvfrom(4096)
    except socket.timeout:
        continue

    try:
        hdr, blocks, _ = decode_frame(data)
    except Exception:
        continue

    st = None
    has_emg = False
    for t, v in blocks:
        if t == BLK_STATUS:
            st = parse_status(v)
        elif t == BLK_EMG_RAW:
            has_emg = True

    if not st or not has_emg:
        continue

    tsrc = st.get("t_src_sample")
    if tsrc is None:
        continue

    win_frames += 1

    if win_tsrc_first is None:
        win_tsrc_first = tsrc
    win_tsrc_last = tsrc

    if prev_seq is not None:
        seq_step = (hdr.seq - prev_seq) & 0xFFFFFFFF
        if seq_step > 1:
            win_seq_gaps += seq_step - 1

    if prev_tsrc is not None:
        step = (tsrc - prev_tsrc) & 0xFFFFFFFF
        win_step_sum += step
        win_step_count += 1
        if step > 8:
            win_tsrc_big_steps += 1

    prev_tsrc = tsrc
    prev_seq = hdr.seq

    now = time.time()
    if now - win_start >= 1.0:
        elapsed = now - win_start
        fps = win_frames / elapsed

        if win_tsrc_first is not None and win_tsrc_last is not None:
            implied_sps = (win_tsrc_last - win_tsrc_first) / elapsed
        else:
            implied_sps = 0.0

        avg_step = (win_step_sum / win_step_count) if win_step_count else 0.0

        print(
            f"fps={fps:.1f} "
            f"avg_tsrc_step={avg_step:.2f} "
            f"implied_sps={implied_sps:.1f} "
            f"seq_gaps={win_seq_gaps} "
            f"big_tsrc_steps={win_tsrc_big_steps}"
        )

        win_start = now
        win_frames = 0
        win_tsrc_first = None
        win_tsrc_last = None
        win_seq_gaps = 0
        win_tsrc_big_steps = 0
        win_step_sum = 0
        win_step_count = 0
