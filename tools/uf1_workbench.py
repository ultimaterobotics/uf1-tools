# Advanced one-device UF1 workbench for calibration/debug use.
# Current focus: single-device EMG/IMU/MAG/QUAT inspection, not polished end-user UX.
# Example:
# PYTHONPATH=src python tools/uf1_workbench.py --bind 0.0.0.0 --port 26750
import argparse
import math
import socket
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import pygame

from uf1.uf1 import decode_frame, BLK_STATUS, BLK_EMG_RAW, parse_status, parse_emg_raw

BLK_IMU_6DOF = 0x03
BLK_MAG_3 = 0x04
BLK_QUAT = 0x05
PORT_DEFAULT = 26750


@dataclass
class DevState:
    device_id: int
    sample_rate_hz: float = 1150.0
    battery_pct: int = 255
    mode: int = 0
    last_status_time: float = 0.0

    emg: Deque[int] = field(default_factory=deque)
    emg_tsrc_last: Optional[int] = None
    emg_frames_win: int = 0
    emg_fps: float = 0.0

    latest_imu: Optional[Tuple[int, int, int, int, int, int]] = None
    latest_mag: Optional[Tuple[int, int, int]] = None
    latest_quat: Optional[Tuple[int, int, int, int]] = None
    last_aux_time: float = 0.0
    aux_frames_win: int = 0
    aux_fps: float = 0.0

    last_rate_t: float = field(default_factory=time.monotonic)

    ax_hist: Deque[int] = field(default_factory=deque)
    ay_hist: Deque[int] = field(default_factory=deque)
    az_hist: Deque[int] = field(default_factory=deque)
    gx_hist: Deque[int] = field(default_factory=deque)
    gy_hist: Deque[int] = field(default_factory=deque)
    gz_hist: Deque[int] = field(default_factory=deque)
    mx_hist: Deque[int] = field(default_factory=deque)
    my_hist: Deque[int] = field(default_factory=deque)
    mz_hist: Deque[int] = field(default_factory=deque)

    def set_sample_rate(self, sr: float):
        if sr > 0:
            self.sample_rate_hz = sr

    def append_emg(self, samples: List[int], tsrc: Optional[int], window_sec: float):
        self.emg.extend(samples)
        max_samples = max(64, int(self.sample_rate_hz * window_sec))
        while len(self.emg) > max_samples:
            self.emg.popleft()
        self.emg_tsrc_last = tsrc
        self.emg_frames_win += 1

    def append_imu(self, imu: Tuple[int, int, int, int, int, int], hist_len: int):
        self.latest_imu = imu
        ax, ay, az, gx, gy, gz = imu
        _append_hist(self.ax_hist, ax, hist_len)
        _append_hist(self.ay_hist, ay, hist_len)
        _append_hist(self.az_hist, az, hist_len)
        _append_hist(self.gx_hist, gx, hist_len)
        _append_hist(self.gy_hist, gy, hist_len)
        _append_hist(self.gz_hist, gz, hist_len)
        self.last_aux_time = time.monotonic()
        self.aux_frames_win += 1

    def append_mag(self, mag: Tuple[int, int, int], hist_len: int):
        self.latest_mag = mag
        mx, my, mz = mag
        _append_hist(self.mx_hist, mx, hist_len)
        _append_hist(self.my_hist, my, hist_len)
        _append_hist(self.mz_hist, mz, hist_len)
        self.last_aux_time = time.monotonic()
        self.aux_frames_win += 1

    def append_quat(self, quat: Tuple[int, int, int, int]):
        self.latest_quat = quat
        self.last_aux_time = time.monotonic()
        self.aux_frames_win += 1

    def update_rates(self, now: float):
        dt = now - self.last_rate_t
        if dt >= 1.0:
            self.emg_fps = self.emg_frames_win / dt
            self.aux_fps = self.aux_frames_win / dt
            self.emg_frames_win = 0
            self.aux_frames_win = 0
            self.last_rate_t = now


def _append_hist(buf: Deque[int], value: int, max_len: int):
    buf.append(value)
    while len(buf) > max_len:
        buf.popleft()


def parse_i16x6(val: bytes) -> Optional[Tuple[int, int, int, int, int, int]]:
    if len(val) != 12:
        return None
    return struct.unpack("<6h", val)


def parse_i16x3(val: bytes) -> Optional[Tuple[int, int, int]]:
    if len(val) != 6:
        return None
    return struct.unpack("<3h", val)


def parse_i16x4(val: bytes) -> Optional[Tuple[int, int, int, int]]:
    if len(val) != 8:
        return None
    return struct.unpack("<4h", val)


def mode_name(mode: int) -> str:
    if mode == 0:
        return "PhoneOpt"
    if mode == 1:
        return "FullStream"
    return f"Mode{mode}"


def compute_fft_bars(
    samples: List[int], n_bins: int = 24, fft_len: int = 128
) -> List[float]:
    if len(samples) < 16:
        return [0.0] * n_bins
    x = samples[-fft_len:]
    n = len(x)
    if n < 16:
        return [0.0] * n_bins

    # Hann window + DC removal
    mean = sum(x) / n
    win = [0.5 - 0.5 * math.cos((2.0 * math.pi * i) / max(1, n - 1)) for i in range(n)]
    xw = [(x[i] - mean) * win[i] for i in range(n)]

    mags: List[float] = []
    max_k = min(n_bins, n // 2 - 1)
    for k in range(1, max_k + 1):
        re = 0.0
        im = 0.0
        for t, v in enumerate(xw):
            ang = 2.0 * math.pi * k * t / n
            re += v * math.cos(ang)
            im -= v * math.sin(ang)
        mags.append(math.sqrt(re * re + im * im) / n)

    while len(mags) < n_bins:
        mags.append(0.0)
    return mags


def draw_text(screen, font, x, y, text, color=(220, 220, 220)):
    screen.blit(font.render(text, True, color), (x, y))


def draw_waveform(screen, rect, samples: List[int], color=(0, 220, 0)):
    pygame.draw.rect(screen, (35, 35, 35), rect, 1)
    x, y, w, h = rect
    if len(samples) < 2:
        return
    mn = min(samples)
    mx = max(samples)
    span = max(1, mx - mn)
    mid = (mx + mn) / 2.0

    def y_of(v: int) -> int:
        return int(y + h / 2 - ((v - mid) / span) * (h * 0.42))

    pts = []
    x_step = w / max(1, len(samples) - 1)
    for i, v in enumerate(samples):
        pts.append((int(x + i * x_step), y_of(v)))
    pygame.draw.line(screen, (60, 60, 60), (x, y + h // 2), (x + w, y + h // 2), 1)
    pygame.draw.lines(screen, color, False, pts, 2)


def draw_bars(screen, rect, vals: List[float], color=(230, 200, 40)):
    pygame.draw.rect(screen, (35, 35, 35), rect, 1)
    x, y, w, h = rect
    if not vals:
        return
    mx = max(vals) if max(vals) > 0 else 1.0
    bar_w = max(1, int(w / len(vals)))
    for i, v in enumerate(vals):
        bh = int((v / mx) * (h - 4))
        rx = x + i * bar_w
        ry = y + h - 2 - bh
        pygame.draw.rect(screen, color, (rx + 1, ry, max(1, bar_w - 2), bh))


def draw_triplet_plot(
    screen, rect, a: Deque[int], b: Deque[int], c: Deque[int], colors, label: str, font
):
    pygame.draw.rect(screen, (35, 35, 35), rect, 1)
    x, y, w, h = rect
    draw_text(screen, font, x + 6, y + 4, label, (180, 180, 180))
    if len(a) < 2:
        return
    vals = list(a) + list(b) + list(c)
    mn = min(vals)
    mx = max(vals)
    span = max(1, mx - mn)
    mid = (mx + mn) / 2.0
    inner_y = y + 20
    inner_h = h - 24

    def plot_one(buf: Deque[int], color):
        pts = []
        data = list(buf)
        x_step = w / max(1, len(data) - 1)
        for i, v in enumerate(data):
            yy = int(inner_y + inner_h / 2 - ((v - mid) / span) * (inner_h * 0.42))
            pts.append((int(x + i * x_step), yy))
        if len(pts) >= 2:
            pygame.draw.lines(screen, color, False, pts, 2)

    pygame.draw.line(
        screen,
        (60, 60, 60),
        (x, inner_y + inner_h // 2),
        (x + w, inner_y + inner_h // 2),
        1,
    )
    plot_one(a, colors[0])
    plot_one(b, colors[1])
    plot_one(c, colors[2])


def draw_mag_xy(screen, rect, mag: Optional[Tuple[int, int, int]], font):
    pygame.draw.rect(screen, (35, 35, 35), rect, 1)
    x, y, w, h = rect
    draw_text(screen, font, x + 6, y + 4, "MAG XY", (180, 180, 180))
    cx = x + w // 2
    cy = y + h // 2 + 8
    r = min(w, h - 20) // 2 - 8
    pygame.draw.circle(screen, (70, 70, 70), (cx, cy), r, 1)
    pygame.draw.line(screen, (60, 60, 60), (cx - r, cy), (cx + r, cy), 1)
    pygame.draw.line(screen, (60, 60, 60), (cx, cy - r), (cx, cy + r), 1)
    if mag is None:
        return
    mx, my, _mz = mag
    scale = max(1.0, max(abs(mx), abs(my)))
    px = int(cx + (mx / scale) * (r * 0.85))
    py = int(cy - (my / scale) * (r * 0.85))
    pygame.draw.circle(screen, (255, 180, 60), (px, py), 5)


def select_next_device(
    device_ids: List[int], current: Optional[int], delta: int
) -> Optional[int]:
    if not device_ids:
        return None
    if current not in device_ids:
        return device_ids[0]
    idx = device_ids.index(current)
    return device_ids[(idx + delta) % len(device_ids)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = run forever")
    ap.add_argument(
        "--window-sec", type=float, default=2.5, help="EMG waveform window length"
    )
    ap.add_argument(
        "--hist-len", type=int, default=180, help="history length for IMU/MAG plots"
    )
    ap.add_argument(
        "--sample-rate",
        type=float,
        default=1150.0,
        help="fallback sample rate when STATUS SR=0",
    )
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.01)

    pygame.init()
    W, H = 1400, 860
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("UF1 Workbench")
    font = pygame.font.SysFont(None, 24)
    font_small = pygame.font.SysFont(None, 20)
    clock = pygame.time.Clock()

    devices: Dict[int, DevState] = {}
    selected_dev: Optional[int] = None
    start_t = time.monotonic()

    running = True
    while running:
        if args.seconds > 0 and (time.monotonic() - start_t) > args.seconds:
            break

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_TAB, pygame.K_RIGHT):
                    selected_dev = select_next_device(
                        sorted(devices.keys()), selected_dev, +1
                    )
                elif event.key == pygame.K_LEFT:
                    selected_dev = select_next_device(
                        sorted(devices.keys()), selected_dev, -1
                    )

        for _ in range(40):
            try:
                data, _src = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                hdr, blocks, _ = decode_frame(data)
            except Exception:
                continue

            st = devices.setdefault(
                hdr.device_id,
                DevState(device_id=hdr.device_id, sample_rate_hz=args.sample_rate),
            )
            now = time.monotonic()

            for bt, val in blocks:
                if bt == BLK_STATUS:
                    status = parse_status(val)
                    st.last_status_time = now
                    sr = status.get("sample_rate_hz", 0)
                    if sr:
                        st.set_sample_rate(float(sr))
                    mode = status.get("mode")
                    if mode is not None:
                        st.mode = mode
                    batt = status.get("battery_pct", 255)
                    if batt != 255:
                        st.battery_pct = batt
                elif bt == BLK_EMG_RAW:
                    emg = parse_emg_raw(val)
                    samples = list(emg.get("samples_i16", []))
                    st.append_emg(samples, st.emg_tsrc_last, args.window_sec)
                elif bt == BLK_IMU_6DOF:
                    imu = parse_i16x6(val)
                    if imu is not None:
                        st.append_imu(imu, args.hist_len)
                elif bt == BLK_MAG_3:
                    mag = parse_i16x3(val)
                    if mag is not None:
                        st.append_mag(mag, args.hist_len)
                elif bt == BLK_QUAT:
                    quat = parse_i16x4(val)
                    if quat is not None:
                        st.append_quat(quat)

            st.update_rates(now)
            if selected_dev is None:
                selected_dev = hdr.device_id

        screen.fill((0, 0, 0))

        dev_ids = sorted(devices.keys())
        if selected_dev not in dev_ids and dev_ids:
            selected_dev = dev_ids[0]

        header = "Devices: "
        if dev_ids:
            pieces = []
            for d in dev_ids:
                mark = "*" if d == selected_dev else " "
                pieces.append(f"{mark}0x{d:08X}")
            header += "   ".join(pieces)
        else:
            header += "(none yet)"
        draw_text(screen, font, 14, 12, header)
        draw_text(
            screen,
            font_small,
            14,
            40,
            "TAB/RIGHT: next device   LEFT: previous   ESC: quit",
            (160, 160, 160),
        )

        if selected_dev is None or selected_dev not in devices:
            draw_text(
                screen, font, 14, 90, f"Listening on UDP {args.port}…", (200, 200, 200)
            )
            pygame.display.flip()
            clock.tick(60)
            continue

        st = devices[selected_dev]
        now = time.monotonic()
        aux_age_ms = (
            int((now - st.last_aux_time) * 1000) if st.last_aux_time > 0 else -1
        )

        draw_text(screen, font, 14, 76, f"Device 0x{selected_dev:08X}")
        draw_text(
            screen,
            font_small,
            14,
            102,
            f'emg_fps={st.emg_fps:.1f}  aux_fps={st.aux_fps:.1f}  sr={st.sample_rate_hz:.1f}Hz  mode={mode_name(st.mode)}  batt={st.battery_pct if st.battery_pct != 255 else "??"}  aux_age={aux_age_ms if aux_age_ms >= 0 else "??"}ms',
            (200, 200, 200),
        )

        waveform_rect = pygame.Rect(14, 138, 940, 330)
        fft_rect = pygame.Rect(14, 484, 940, 150)

        emg_samples = list(st.emg)
        draw_waveform(screen, waveform_rect, emg_samples)
        draw_text(
            screen,
            font_small,
            waveform_rect.x + 6,
            waveform_rect.y + 6,
            "EMG waveform",
            (180, 180, 180),
        )

        bars = compute_fft_bars(emg_samples)
        draw_bars(screen, fft_rect, bars)
        draw_text(
            screen,
            font_small,
            fft_rect.x + 6,
            fft_rect.y + 6,
            "Local spectrum",
            (180, 180, 180),
        )

        right_x = 970
        draw_triplet_plot(
            screen,
            pygame.Rect(right_x, 138, 410, 145),
            st.ax_hist,
            st.ay_hist,
            st.az_hist,
            [(220, 80, 80), (80, 220, 80), (80, 140, 240)],
            "ACC xyz",
            font_small,
        )
        draw_triplet_plot(
            screen,
            pygame.Rect(right_x, 300, 410, 145),
            st.gx_hist,
            st.gy_hist,
            st.gz_hist,
            [(220, 80, 80), (80, 220, 80), (80, 140, 240)],
            "GYRO xyz",
            font_small,
        )
        draw_triplet_plot(
            screen,
            pygame.Rect(right_x, 462, 410, 145),
            st.mx_hist,
            st.my_hist,
            st.mz_hist,
            [(220, 80, 80), (80, 220, 80), (80, 140, 240)],
            "MAG xyz",
            font_small,
        )
        draw_mag_xy(
            screen, pygame.Rect(right_x, 624, 190, 180), st.latest_mag, font_small
        )

        info_rect = pygame.Rect(right_x + 205, 624, 175, 180)
        pygame.draw.rect(screen, (35, 35, 35), info_rect, 1)
        draw_text(
            screen,
            font_small,
            info_rect.x + 8,
            info_rect.y + 8,
            "Latest values",
            (180, 180, 180),
        )

        y = info_rect.y + 34
        if st.latest_imu is not None:
            ax, ay, az, gx, gy, gz = st.latest_imu
            draw_text(screen, font_small, info_rect.x + 8, y, f"ax {ax:6d}")
            draw_text(screen, font_small, info_rect.x + 8, y + 18, f"ay {ay:6d}")
            draw_text(screen, font_small, info_rect.x + 8, y + 36, f"az {az:6d}")
            draw_text(screen, font_small, info_rect.x + 92, y, f"gx {gx:6d}")
            draw_text(screen, font_small, info_rect.x + 92, y + 18, f"gy {gy:6d}")
            draw_text(screen, font_small, info_rect.x + 92, y + 36, f"gz {gz:6d}")
        else:
            draw_text(screen, font_small, info_rect.x + 8, y, "IMU: --")

        y += 66
        if st.latest_mag is not None:
            mx, my, mz = st.latest_mag
            draw_text(screen, font_small, info_rect.x + 8, y, f"mx {mx:6d}")
            draw_text(screen, font_small, info_rect.x + 8, y + 18, f"my {my:6d}")
            draw_text(screen, font_small, info_rect.x + 8, y + 36, f"mz {mz:6d}")
        else:
            draw_text(screen, font_small, info_rect.x + 8, y, "MAG: --")

        y += 66
        if st.latest_quat is not None:
            qw, qx, qy, qz = st.latest_quat
            draw_text(screen, font_small, info_rect.x + 8, y, f"qw {qw:6d}")
            draw_text(screen, font_small, info_rect.x + 8, y + 18, f"qx {qx:6d}")
            draw_text(screen, font_small, info_rect.x + 92, y, f"qy {qy:6d}")
            draw_text(screen, font_small, info_rect.x + 92, y + 18, f"qz {qz:6d}")
        else:
            draw_text(screen, font_small, info_rect.x + 8, y, "QUAT: --")

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
