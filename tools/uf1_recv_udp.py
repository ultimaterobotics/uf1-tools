import argparse
import socket
import time
from uf1.uf1 import decode_frame, BLK_STATUS, BLK_EMG_RAW, parse_status, parse_emg_raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=26750)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.5)

    start = time.monotonic()
    frames = 0
    last_seq = None
    drops = 0

    while time.monotonic() - start < args.seconds:
        try:
            data, src = sock.recvfrom(4096)
        except socket.timeout:
            continue

        try:
            hdr, blocks, _ = decode_frame(data)
        except Exception as e:
            print("Decode error:", e)
            continue

        frames += 1
        if last_seq is not None and hdr.seq != (last_seq + 1) % (1 << 32):
            drops += (hdr.seq - last_seq - 1) & 0xFFFFFFFF
        last_seq = hdr.seq

        # quick parse for sanity
        status = None
        emg = None
        for bt, val in blocks:
            if bt == BLK_STATUS:
                status = parse_status(val)
            elif bt == BLK_EMG_RAW:
                emg = parse_emg_raw(val)

        if frames % 50 == 0:
            print(
                f"frames={frames} drops={drops} dev={hdr.device_id:#x} seq={hdr.seq} "
                f"t_us={hdr.t_us} t_src={status['t_src_sample'] if status else None} "
                f"emg_n={len(emg['samples_i16']) if emg else None}"
            )

    print(f"Done. frames={frames} drops={drops}")


if __name__ == "__main__":
    main()
