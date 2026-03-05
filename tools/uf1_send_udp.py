import argparse
import socket
from uf1_gen import generate_frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=26750)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--device-id", type=lambda x: int(x, 0), default=0x12345678)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)

    sent = 0
    for frame in generate_frames(device_id=args.device_id, seconds=args.seconds):
        sock.sendto(frame, addr)
        sent += 1

    print(f"Sent {sent} UF1 frames to {addr[0]}:{addr[1]}")


if __name__ == "__main__":
    main()
