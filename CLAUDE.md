# uf1-tools

Python + browser workbench and protocol tools for UF1 — the canonical transport for uMyo data.
Receives UF1 frames over UDP from the Android app.
Multi-device aware — handles N simultaneous device streams keyed by device ID.

## Setup

```bash
cd uf1-tools
python3 -m venv venv
source venv/bin/activate
export PYTHONPATH=src
pip install -r requirements.txt
```

`PYTHONPATH=src` is required every session — the `uf1` module lives in `src/uf1/` and is not installed as a package.

## Main scripts

| Script | Purpose |
|---|---|
| `uf1_workbench_server.py` | WebSocket bridge — reads UDP stream, pushes device state to browser at 20fps |
| `umyo_workbench.html` | Browser workbench — opens automatically, one row per device |
| `uf1_probe.py` | Validation tool — per-device stats, seq gaps, fps, device names |
| `uf1_view.py` | Simple EMG-only viewer (pygame, single device) |

## Running the workbench

```bash
cd uf1-tools
source venv/bin/activate
export PYTHONPATH=src
python tools/uf1_workbench_server.py
# opens browser automatically — start streaming from Android app
```

Optional flags: `--no-browser`, `--udp-port 26750`, `--ws-port 8765`, `--bind 0.0.0.0`

## Running the probe

```bash
python tools/uf1_probe.py              # default: 0.0.0.0:26750
python tools/uf1_probe.py --port 26751
```

## ⚠️ Start Python before Android streaming

The Android app sends the 0x07 DEVICE_NAME frame once when streaming starts.
If you start the workbench or probe after tapping Start Streaming, you miss the name frame and see hex IDs instead of names.

**Always start the Python tool first, then tap Start Streaming in the app.**

The Android app also re-sends the name frame every ~5s, so names will appear shortly after reconnect.

## UF1 protocol — block types

| Block ID | Name | Description |
|---|---|---|
| 0x01 | EMG_RAW | Raw EMG samples |
| 0x03 | IMU | Accelerometer + Gyroscope (ax/ay/az/gx/gy/gz, int16) |
| 0x04 | MAG | Magnetometer XYZ (mx/my/mz, int16) |
| 0x05 | QUAT | Quaternion orientation (qw/qx/qy/qz, int16 ±32767 = ±1.0) |
| 0x07 | DEVICE_NAME | UTF-8 device name, 1–32 bytes, no null terminator |
| 0xF0 | BLE_ADV_RAW | Raw BLE advertisement data |
| 0xF1 | DEBUG | Debug/unknown payload |

## Multi-device

All tools key device state by `device_id` (uint32, CRC of MAC). Up to N devices simultaneously — 3 confirmed working. Devices not seen for 5s are dropped.

## Probe output fields

```
dev=0xDFACE6DB (uMyo-E14A) frames_total=384 fps_total=182.7 emg_fps=155.8
quat_fps=9.0 aux_fps=18.0 avg_tsrc_step=8.00 implied_sps=1238.3
seq_gaps_all=0 emg_missing_chunks=0 big_tsrc_steps=0 bad_tsrc_steps=0
batt=?? mode=PhoneOpt sr=1150.0Hz
```

- `seq_gaps_all=0` — no sequence gaps, clean stream
- `emg_missing_chunks` — inferred missing EMG chunks from tsrc steps
- `implied_sps` — implied sample rate from timestamp deltas
- `batt=??` — battery % not yet filled by firmware
- `quat_fps` — target is 20-25fps; under 10 indicates BLE scheduler issues

## Workbench architecture

`uf1_workbench_server.py` — Python WebSocket server:
- Reads UDP on port 26750 (same as `uf1_probe.py`)
- Parses via `uf1.uf1`: `decode_frame()`, `parse_emg_raw()`, `parse_status()`, `parse_device_name()`, plus direct `struct.unpack` for QUAT (`<4h`), IMU_6DOF (`<6h`), MAG_3 (`<3h`)
- Broadcasts JSON state to all WebSocket clients at 20fps
- EMG samples accumulated between broadcasts — full queue flushed per broadcast
- Seq gaps tracked per-packet with uint16 delta mask
- Module-level `name_cache` — persists names across device reconnects
- `_quat_fresh` / `_aux_fresh` flags per device — set on new block arrival, cleared after each broadcast. Used by browser to distinguish fresh data from replayed last state.

`umyo_workbench.html` — browser GUI:
- Dark instrument theme, stacked rows (one per device)
- Per row: device name + ID, streaming badge, metrics (EMG fps, quat fps, seq gaps, avg tsrc Δ, battery), EMG waveform canvas, spectrum bars, ACC/GYRO sparklines (x/y/z colored), 3D orientation cube
- 3D cube: real quaternion from firmware (int16 → normalized unit quat → 3×3 rotation matrix → cube vertices). Falls back to animated sim rotation when no fresh quat received within 500ms.
- Sparklines: only update when `aux_fresh` flag is true in WS message — stale last frame stays visible rather than blanking
- EMG: auto-scale min/max per buffer, 2875-sample buffer (~2.5s at 1150Hz)
- Speed slider (1–20, default 6) — controls sim drip rate only, live mode pushes all samples immediately
- Details toggle: Hz axis labels on spectrum + raw quat values below cube
- Calibration overlay (per device)
- Record button and Export CSV (UI present, file write not yet implemented)
- Falls back to 3-device simulation when WebSocket not available

## WebSocket protocol

- Direction: server → browser only
- Rate: ~20fps (50ms interval)
- Format: JSON — `{"type": "state", "devices": [...]}`

Device object fields:

| Field | Type | Description |
|---|---|---|
| `id` | string | `"0xDFACE6DB"` |
| `name` | string | From 0x07 block, else hex ID |
| `emg_fps` | int | EMG frames/s |
| `quat_fps` | int | QUAT frames/s |
| `fps_total` | int | All frames/s |
| `seq_gaps` | int | Cumulative sequence gaps |
| `emg_missing_chunks` | int | Inferred missing EMG chunks |
| `avg_tsrc_step` | float | Mean tsrc delta per EMG frame |
| `batt` | int\|null | Battery % (null if unknown) |
| `mode` | string | `"PhoneOpt"` or `"FullStream"` |
| `sr` | float | Sample rate Hz |
| `emg` | int[] | Latest EMG chunk (raw int16) |
| `quat` | object | `{qw, qx, qy, qz}` raw int16 |
| `quat_fresh` | bool | True only when a new QUAT block arrived this tick |
| `aux` | object | `{ax, ay, az, gx, gy, gz}` raw int16; `mx, my, mz` added when MAG present |
| `aux_fresh` | bool | True only when a new AUX block arrived this tick |

## What's not yet done

- Export CSV / Record — buttons present in workbench, file write not implemented
- Battery % — firmware not yet filling STATUS.battery_pct
- Rename UI — write to FBD02 characteristic not wired in Android app
- Base station (USB) mode in workbench — USB base path not wired to new GUI
- Direct PC BLE — no Android bridge needed, planned
