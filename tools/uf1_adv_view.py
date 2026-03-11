import argparse
import socket
import time
from typing import Optional, Tuple, Dict

import pygame

from uf1.uf1 import decode_frame

ADV_BLOCK = 0xF0


def parse_ad_structures(raw: bytes):
    """Parse BLE advertising AD structures from raw scanRecord bytes."""
    i = 0
    n = len(raw)
    while i < n:
        ln = raw[i]
        i += 1
        if ln == 0:
            break
        if i + ln > n:
            break
        ad_type = raw[i]
        ad_data = raw[i + 1 : i + ln]
        i += ln
        yield ad_type, ad_data


def find_name_and_mfg(raw: bytes) -> Tuple[Optional[str], Optional[bytes]]:
    name = None
    mfg = None
    for t, v in parse_ad_structures(raw):
        if t in (0x08, 0x09):  # short/complete local name
            try:
                name = v.decode("utf-8", errors="replace")
            except Exception:
                name = repr(v)
        elif t == 0xFF:
            mfg = v
    return name, mfg


def decode_umyo_mfg15(mfg: Optional[bytes]) -> Optional[Dict[str, object]]:
    """
    uMyoBleSdk layout (15 bytes):
      [0]  dataID (u8)
      [1]  battery (u8 0..255)
      [2]  sp0_hi (u8) -> sp0 = byte<<8
      [3]  muscle (u8)
      [4..5] sp1 (u16 BE)
      [6..7] sp2 (u16 BE)
      [8..9] sp3 (u16 BE)
      [10..11] qw (u16 BE)
      [12] qx_hi (u8) -> qx = byte<<8
      [13] qy_hi (u8) -> qy = byte<<8
      [14] qz_hi (u8) -> qz = byte<<8
    """
    if mfg is None or len(mfg) < 15:
        return None

    dataID = mfg[0]
    batt = mfg[1]
    sp0 = mfg[2] << 8
    muscle = mfg[3]
    sp1 = (mfg[4] << 8) | mfg[5]
    sp2 = (mfg[6] << 8) | mfg[7]
    sp3 = (mfg[8] << 8) | mfg[9]
    qw = (mfg[10] << 8) | mfg[11]
    qx = mfg[12] << 8
    qy = mfg[13] << 8
    qz = mfg[14] << 8

    return {
        "dataID": dataID,
        "battery_pct": batt * 100.0 / 255.0,
        "muscle": muscle,  # 0..255
        "spectrum": (sp0, sp1, sp2, sp3),
        "quat": (qw, qx, qy, qz),
    }


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=26750)
    ap.add_argument("--fps", type=int, default=60)
    args = ap.parse_args()

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.01)

    # Pygame
    pygame.init()
    W, H = 900, 520
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("uMyo BLE Adv Telemetry (UF1)")
    font = pygame.font.SysFont(None, 22)
    font_big = pygame.font.SysFont(None, 28)
    clock = pygame.time.Clock()

    # State
    last = {
        "name": None,
        "device_id": None,
        "rssi": None,
        "dataID": None,
        "battery_pct": None,
        "muscle": None,
        "spectrum": (0, 0, 0, 0),
        "quat": (0, 0, 0, 0),
        "last_rx": time.monotonic(),
    }

    frames = 0
    last_fps_t = time.monotonic()
    frames_last = 0
    fps_est = 0.0

    running = True
    while running:
        # events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        # drain UDP
        for _ in range(50):
            try:
                data, src = sock.recvfrom(4096)
            except socket.timeout:
                break

            try:
                hdr, blocks, _ = decode_frame(data)
            except Exception:
                continue

            adv_val = None
            for t, v in blocks:
                if t == ADV_BLOCK:
                    adv_val = v
                    break
            if not adv_val or len(adv_val) < 3:
                continue

            rssi = int.from_bytes(adv_val[2:3], "little", signed=True)
            raw = adv_val[3:]

            name, mfg = find_name_and_mfg(raw)
            decoded = decode_umyo_mfg15(mfg)
            if decoded:
                last.update(
                    {
                        "name": name,
                        "device_id": hdr.device_id,
                        "rssi": rssi,
                        "dataID": decoded["dataID"],
                        "battery_pct": decoded["battery_pct"],
                        "muscle": decoded["muscle"],
                        "spectrum": decoded["spectrum"],
                        "quat": decoded["quat"],
                        "last_rx": time.monotonic(),
                    }
                )
                frames += 1

        # fps estimate
        now = time.monotonic()
        if now - last_fps_t >= 1.0:
            fps_est = (frames - frames_last) / (now - last_fps_t)
            frames_last = frames
            last_fps_t = now

        # draw background
        screen.fill((0, 0, 0))

        # title
        title = "uMyo BLE Advertisement Telemetry"
        screen.blit(font_big.render(title, True, (220, 220, 220)), (16, 12))

        # connection status
        age = now - last["last_rx"]
        connected = age < 1.0
        status_color = (0, 200, 0) if connected else (200, 80, 80)
        status_txt = f"rx_fps≈{fps_est:.1f}  last_rx={age:.2f}s ago"
        screen.blit(font.render(status_txt, True, status_color), (16, 44))

        # device line
        dev = last["device_id"]
        dev_hex = f"0x{dev:08x}" if isinstance(dev, int) else "—"
        name = last["name"] or "—"
        rssi = last["rssi"]
        rssi_str = f"{rssi} dBm" if rssi is not None else "—"
        batt = last["battery_pct"]
        batt_str = f"{batt:.1f}%" if batt is not None else "—"
        dataID = last["dataID"]
        did_str = str(dataID) if dataID is not None else "—"

        line = f"device={dev_hex}  name={name}  rssi={rssi_str}  batt={batt_str}  dataID={did_str}"
        screen.blit(font.render(line, True, (200, 200, 200)), (16, 74))

        # Muscle meter
        muscle = last["muscle"]
        muscle_val = int(muscle) if muscle is not None else 0
        muscle_norm = clamp(muscle_val / 255.0, 0.0, 1.0)

        mx, my = 16, 120
        mw, mh = 860, 40
        pygame.draw.rect(screen, (60, 60, 60), (mx, my, mw, mh), 2)
        pygame.draw.rect(
            screen, (0, 180, 0), (mx + 2, my + 2, int((mw - 4) * muscle_norm), mh - 4)
        )
        screen.blit(
            font_big.render(f"Muscle level: {muscle_val}/255", True, (230, 230, 230)),
            (mx, my - 28),
        )

        # FFT 4 bars
        sp0, sp1, sp2, sp3 = last["spectrum"]
        sps = [sp0, sp1, sp2, sp3]
        sp_max = max(1, max(sps))
        bx, by = 16, 210
        bw = 200
        gap = 18
        bar_w = 180
        bar_h = 180
        screen.blit(
            font_big.render("FFT bins (4)", True, (230, 230, 230)), (bx, by - 32)
        )

        for i, v in enumerate(sps):
            # normalize to max (log would look nicer later)
            norm = clamp(v / sp_max, 0.0, 1.0)
            x = bx + i * (bw + gap)
            y = by
            pygame.draw.rect(screen, (60, 60, 60), (x, y, bar_w, bar_h), 2)
            pygame.draw.rect(
                screen,
                (0, 140, 255),
                (
                    x + 2,
                    y + bar_h - 2 - int((bar_h - 4) * norm),
                    bar_w - 4,
                    int((bar_h - 4) * norm),
                ),
            )
            screen.blit(font.render(f"{v}", True, (200, 200, 200)), (x, y + bar_h + 6))

        # QUAT display
        qw, qx, qy, qz = last["quat"]
        qx0, qy0 = 16, 430
        qtxt = f"quat (raw): w={qw} x={qx} y={qy} z={qz}"
        screen.blit(font.render(qtxt, True, (200, 200, 200)), (qx0, qy0))

        pygame.display.flip()
        clock.tick(args.fps)

    pygame.quit()


if __name__ == "__main__":
    main()
