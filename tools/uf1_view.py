import argparse
import socket
import time
from collections import deque

import pygame

from uf1.uf1 import decode_frame, BLK_STATUS, BLK_EMG_RAW, parse_status, parse_emg_raw

PORT_DEFAULT = 26750


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = run forever")
    ap.add_argument(
        "--window-sec", type=float, default=2.5, help="waveform window length"
    )
    ap.add_argument(
        "--sample-rate", type=float, default=1150.0, help="fallback SR if STATUS SR=0"
    )
    args = ap.parse_args()

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.01)

    # Pygame setup
    pygame.init()
    W, H = 1100, 500
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("UF1 Live EMG Viewer")
    font = pygame.font.SysFont(None, 22)
    clock = pygame.time.Clock()

    # Rolling buffer of samples
    # We'll store raw int16 samples; drawing uses autoscale
    buf = deque()

    last_seq = None
    drops = 0
    frames = 0
    fps = 0.0
    last_fps_t = time.monotonic()
    last_fps_frames = 0

    last_status = {"battery_pct": 255, "mode": 0, "t_src_sample": 0}
    sample_rate = args.sample_rate

    start_t = time.monotonic()

    running = True
    while running:
        # exit after seconds if requested
        if args.seconds > 0 and (time.monotonic() - start_t) > args.seconds:
            break

        # Pygame events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        # Read UDP packets (drain a few per frame)
        for _ in range(20):
            try:
                data, _src = sock.recvfrom(4096)
            except socket.timeout:
                break

            try:
                hdr, blocks, _ = decode_frame(data)
            except Exception:
                continue

            frames += 1

            # seq drops
            if last_seq is not None and hdr.seq != (last_seq + 1) % (1 << 32):
                drops += (hdr.seq - last_seq - 1) & 0xFFFFFFFF
            last_seq = hdr.seq

            status = None
            emg = None
            for bt, val in blocks:
                if bt == BLK_STATUS:
                    status = parse_status(val)
                elif bt == BLK_EMG_RAW:
                    emg = parse_emg_raw(val)

            if status:
                last_status = status
                sr = status.get("sample_rate_hz", 0)
                if sr:
                    sample_rate = float(sr)

            if emg:
                buf.extend(emg["samples_i16"])

        # Update FPS calc
        now = time.monotonic()
        if now - last_fps_t >= 1.0:
            fps = (frames - last_fps_frames) / (now - last_fps_t)
            last_fps_t = now
            last_fps_frames = frames

        # Keep buffer limited to window length
        max_samples = int(args.window_sec * sample_rate)
        while len(buf) > max_samples:
            buf.popleft()

        # Draw
        screen.fill((0, 0, 0))

        # If we have samples, draw waveform
        if len(buf) >= 2:
            samples = list(buf)
            # autoscale
            mn = min(samples)
            mx = max(samples)
            span = max(1, mx - mn)
            mid = (mx + mn) / 2.0

            # Map sample -> y
            def y_of(v):
                # center at mid, scale to ~80% height
                return int(H / 2 - ((v - mid) / span) * (H * 0.40))

            # Draw polyline
            x_step = W / max(1, len(samples) - 1)
            pts = []
            for i, v in enumerate(samples):
                x = int(i * x_step)
                y = y_of(v)
                pts.append((x, y))
            pygame.draw.lines(screen, (0, 255, 0), False, pts, 2)

            # center line
            pygame.draw.line(screen, (60, 60, 60), (0, H // 2), (W, H // 2), 1)

        # HUD text
        batt = last_status.get("battery_pct", 255)
        mode = last_status.get("mode", 0)
        mode_str = "PhoneOpt" if mode == 0 else ("Full" if mode == 1 else f"Mode{mode}")

        hud = f"fps={fps:.1f} frames={frames} drops={drops} batt={batt if batt!=255 else '??'} mode={mode_str} sr={sample_rate:.1f}Hz"
        txt = font.render(hud, True, (200, 200, 200))
        screen.blit(txt, (10, 10))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
