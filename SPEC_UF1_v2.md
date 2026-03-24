# uDevices Unified Frame v2 (UF1) Spec

## Changelog from v1

| # | Change |
|---|--------|
| 1 | **Architectural clarification**: UF1 is a toolchain interface format, not a firmware wire format. Added Adapter Model section. |
| 2 | **New section**: Raw device formats — BLE GATT and base station serial documented as first-class spec content. |
| 3 | **BLE GATT raw format**: format byte added at payload position 0 to identify profile without relying on payload size inference. |
| 4 | **Transport section**: serial (base station) transport added. MTU requirements documented. |
| 5 | **STATUS.battery_pct**: 255=unknown is startup-only sentinel; valid range 0–100 now expected from firmware. |
| 6 | **STATUS.mode**: 0 = S1 (low-MTU), 1 = S2 (full stream); adapter must populate from format byte. |
| 7 | **STATUS.sample_rate_hz**: documented as actual negotiated rate, not a hardcoded value. |
| 8 | **Timing policy**: clarifying note added for co-emitted frames from a single raw device packet. |
| 9 | **Block 0xF1**: defined as vendor experimental/debug. Range 0xF1–0xFE reserved for vendor use. |
| 10 | **New section**: Known adapter implementations. |
| 11 | v1 Profile A / Profile B terminology replaced by S1 / S2 to match implementation. |

Header, TLV structure, block types 0x01–0x07 and 0xF0, and CRC32 behavior are unchanged.

---

## 1. Goals

One binary frame format for phone mode, bridge, Windows tools, and future dongles.

Supports "profiles" by including different blocks and rates.

Makes timing robust for gesture training (source counter + rx time).

Enables third-party device integration: any adapter that reads a device's raw format and emits valid UF1 UDP is a conforming adapter.

---

## 2. Adapter Model

**UF1 is not a wire format. Firmware does not send UF1.**

The canonical architecture is:

```
Firmware  →  raw binary (GATT / serial)  →  Adapter  →  UF1 UDP  →  Tools
```

- **Firmware** sends raw binary: GATT notifications over BLE, or legacy serial over the base station.
- **Adapter** reads the raw device format and constructs valid UF1 frames for UDP transmission.
- **UF1** is the toolchain interface: everything above the adapter sees only UF1.

Any software that reads a device's raw format and outputs valid UF1 UDP frames is a **conforming adapter**. This is the mechanism by which third-party devices (fitness trackers, MyoWare, etc.) will plug into the ecosystem: write an adapter, emit UF1.

Adapters are responsible for:

- Constructing valid UF1 frame headers (magic, version, hdr_len, flags, device_id, seq, t_us)
- Wrapping device data in the appropriate TLV blocks
- Populating STATUS fields from whatever the raw device format provides
- Assigning a stable `device_id` per physical device (recommended: `CRC32(MAC_address_string)`)
- Incrementing `seq` monotonically per device stream

---

## 3. Endianness

All multi-byte fields in UF1 frames are **little-endian**.

Exception: the raw nRF24 base station payload (documented in Section 9) is big-endian. This is a firmware artifact, not a UF1 property.

---

## 4. Frame Header (24 bytes)

| Field | Offset | Size | Type | Notes |
|-------|--------|------|------|-------|
| magic | 0 | 2 | u8[2] | 0x55 0x44 ("UD") |
| version | 2 | 1 | u8 | 2 |
| hdr_len | 3 | 1 | u8 | 24 |
| frame_len | 4 | 2 | u16 | total bytes including header (+ CRC if present) |
| flags | 6 | 2 | u16 | see below |
| device_id | 8 | 4 | u32 | uMyo unit ID |
| stream_id | 12 | 1 | u8 | 0 for now |
| reserved | 13 | 1 | u8 | 0 |
| seq | 14 | 4 | u32 | increments per frame per device |
| t_us | 18 | 6 | u48 | monotonic rx_time in microseconds at frame creator hop |

### Flags (u16)

- **bit0**: TIME_SRC_PRESENT — STATUS contains a valid t_src_sample
- **bit1**: TIME_US_IS_RX — always 1 in v2
- **bit2**: CRC32_PRESENT — CRC32 appended after last TLV
- bits 3–15: reserved, set to 0

t_us wrap: 48-bit µs wraps after ~8.9 years.

---

## 5. Payload Blocks (TLV)

Repeated until end of frame (or before CRC):

```
type   u8
len    u16 LE   (value bytes only — does not include type or len fields)
value  bytes[len]
```

Parsers MUST ignore unknown type IDs using `len` to skip. This is the extension mechanism.

---

## 6. Block Layouts

### 0x06 STATUS (len = 10)

| Offset | Size | Field | Type | Notes |
|--------|------|-------|------|-------|
| 0 | 4 | t_src_sample | u32 | sample counter of first EMG sample in accompanying EMG_RAW frame; valid only when header flag TIME_SRC_PRESENT is set |
| 4 | 2 | sample_rate_hz | u16 | actual negotiated sample rate in Hz; 0 = unknown |
| 6 | 1 | battery_pct | u8 | 0–100; 255 = not yet available (startup only) |
| 7 | 1 | rssi_dbm | i8 | -128 = unknown |
| 8 | 1 | mode | u8 | 0 = S1 (low-MTU / PhoneOpt), 1 = S2 (full stream), 2 = other |
| 9 | 1 | status_flags | u8 | bit0 cal_ok, bit1 imu_ok, bit2 mag_ok |

Set header flag `TIME_SRC_PRESENT` when t_src_sample is valid. Do not set it when t_src_sample is 0 or meaningless.

### 0x01 EMG_RAW

| Offset | Size | Field | Type | Notes |
|--------|------|-------|------|-------|
| 0 | 1 | channel_count | u8 | v2: 1 |
| 1 | 1 | samples_per_ch | u8 | typically 8 |
| 2 | 1 | sample_format | u8 | 1 = int16 |
| 3 | 1 | reserved | u8 | 0 |
| 4 | 2*N | samples | int16[] | N = channel_count × samples_per_ch, little-endian |

Typical v2: 1 ch, 8 samples → len = 4 + 16 = 20 bytes.

### 0x02 EMG_FFT4 (len = 8)

4× int16: bin0, bin1, bin2, bin3

### 0x03 IMU_6DOF (len = 12)

6× int16: ax, ay, az, gx, gy, gz

### 0x04 MAG_3 (len = 6)

3× int16: mx, my, mz

### 0x05 QUAT (len = 8)

4× int16 (scaled ±32767 = ±1.0) in order: w, x, y, z

### 0x07 DEVICE_NAME (len = 1–32)

UTF-8 string. No null terminator. Sent once per device at stream start; may be re-sent periodically.
Receivers MUST accept any len in [1, 32]; silently ignore if len = 0 or len > 32.
Intended as a human-readable label (e.g. BLE advertised name or user-assigned alias).

### 0xF0 BLE_ADV_RAW (experimental)

Used to forward raw BLE advertisement data without decoding on the sender side.

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| 0 | 2 | manufacturer_id | u16 LE; 0xFFFF if unknown |
| 2 | 1 | rssi_dbm | i8; -128 if unknown |
| 3 | var | adv_bytes | raw BLE AD structures from scanner (e.g. Android ScanRecord.bytes) |

`device_id` in the UF1 header SHOULD be stable per advertising device; recommended: `CRC32(MAC_address_string)`.
`TIME_SRC_PRESENT` is typically unset for ADV frames.

Device-specific decoders MAY interpret `adv_bytes` and emit additional UF1 blocks for higher-level consumers. Decoders live outside the core UF1 transport.

### 0xF1–0xFE Vendor Experimental Range

**0xF1** is defined as vendor experimental / debug: opaque payload, no required format. Use for debug data, unknown bridged payloads, or private extensions during development.

Type IDs **0xF1–0xFE** are reserved for vendor use. Do not assign them in the core spec.

**0xFF** is reserved.

---

## 7. Timing Policy

`t_us` is the monotonic receive-time timestamp at the adapter (frame creator) in microseconds. `t_src_sample` in STATUS is the sensor's own sample counter at the first sample in the accompanying EMG_RAW block.

Together they support time-alignment of multi-device and multi-stream recordings: `t_src_sample` gives intra-device sample precision; `t_us` gives inter-device wall-clock alignment.

**Co-emitted frames:** When a single raw device packet is split into multiple UF1 frames (e.g. separate EMG and QUAT frames from one GATT notification), all co-emitted frames MUST carry the same `t_src_sample` value in their STATUS block, with `TIME_SRC_PRESENT=1`. This is required for correct time alignment of multi-stream data in gesture training and analysis tools.

---

## 8. CRC32 (optional)

If header flag `CRC32_PRESENT` is set:

- Append `crc32` u32 LE at the end of the frame
- CRC computed over all bytes from magic through the last TLV byte (excluding the CRC field itself)
- On CRC fail: drop frame and count as loss. Do not attempt repair.

CRC is off by default in tools.

---

## 9. Raw Device Formats

These are first-class spec content. Adapters MUST parse them correctly. The formats are what the physical devices emit; adapters convert them to UF1.

### 9.1 BLE GATT Raw Format (uMyo)

uMyo sends raw binary GATT notifications over a custom characteristic. **There is no UF1 framing in the firmware.** The adapter (Android app or future PC dongle) wraps these into UF1 frames.

All GATT payload fields are **little-endian**.

#### Format byte

As of firmware v2, all GATT payloads begin with a **format byte** at position 0 identifying the profile:

| Value | Profile | Minimum MTU | Description |
|-------|---------|-------------|-------------|
| 0x01 | S1 | 20 bytes | Low-MTU fallback: EMG + QUAT combined |
| 0x02 | S2 EMG | 53 bytes | Standard EMG payload |
| 0x03 | S2 aux | 27 bytes | IMU + MAG + QUAT payload |

Firmware auto-selects profile based on negotiated MTU. **S2 is standard; S1 is the supported low-MTU fallback** for constrained connections (field conditions, older centrals). S1 is a capability tier, not a deprecated path.

#### S1 Profile (format byte 0x01, total 61 bytes)

```
[0]       0x01        format byte
[1..4]    tsrc_base   u32 LE — sample counter of first EMG sample in this packet
[5..52]   emg         3 × 8 × int16 LE — 24 raw EMG samples (3 chunks of 8)
[53..60]  quat        w, x, y, z — 4 × int16 LE
```

Adapter mapping:
- Emit one EMG_RAW frame: STATUS (t_src_sample = tsrc_base, TIME_SRC_PRESENT=1, mode=0) + EMG_RAW (1 ch, 24 samples)
- Emit one QUAT frame: STATUS (t_src_sample = tsrc_base, TIME_SRC_PRESENT=1, mode=0) + QUAT

#### S2 EMG Profile (format byte 0x02, total 53 bytes)

```
[0]       0x02        format byte
[1..4]    tsrc_base   u32 LE — sample counter of first EMG sample
[5..52]   emg         3 × 8 × int16 LE — 24 raw EMG samples (3 chunks of 8)
```

Adapter mapping:
- Emit one EMG_RAW frame: STATUS (t_src_sample = tsrc_base, TIME_SRC_PRESENT=1, mode=1) + EMG_RAW (1 ch, 24 samples)

#### S2 Aux Profile (format byte 0x03, total 27 bytes)

```
[0]       0x03        format byte
[1..12]   imu         ax, ay, az, gx, gy, gz — 6 × int16 LE
[13..18]  mag         mx, my, mz — 3 × int16 LE
[19..26]  quat        w, x, y, z — 4 × int16 LE
```

Adapter mapping:
- Emit three frames (each with STATUS mode=1, t_src_sample=0, TIME_SRC_PRESENT=0):
  - STATUS + IMU_6DOF
  - STATUS + MAG_3
  - STATUS + QUAT

Note: S2 aux has no sample counter. t_src_sample is unavailable for these frames; leave TIME_SRC_PRESENT unset.

#### MTU requirements

BLE central MUST negotiate MTU ≥ 20 bytes (S1 fallback) or ≥ 53 bytes (S2 standard). CSR8510-class USB dongles and HM-10 modules do not support sufficient MTU for S2 and are not supported for full-stream mode.

### 9.2 Base Station Serial Format

The base station (udevices_base) receives nRF24 radio packets and forwards them over USB serial as a transparent bridge. **It does not apply UF1 framing.**

**Baud rate:** 921,600

**Serial frame:**

```
[0]     0x4F          sync byte 1
[1]     0xD5          sync byte 2
[2]     rssi          u8 — raw NRF_RADIO->RSSISAMPLE register value
                           NOTE: this is NOT in dBm. It is a raw 7-bit ADC
                           reading from the nRF52 RSSI sample register.
                           Typical range 0–127.
[3…]    nrf24_payload raw nRF24 payload, length = payload[1]
```

**nRF24 payload layout (62 bytes, all fields big-endian):**

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| 0 | 1 | packet_id | u8, rolling 0–128 |
| 1 | 1 | packet_len | u8, total payload length |
| 2–5 | 4 | unit_id | u32 BE — NRF_FICR->DEVICEID[1] |
| 6 | 1 | packet_type | u8 — 80 + send_cnt; valid range 80–120 |
| 7 | 1 | param_id | u8 — parameter block ID (0 = battery) |
| 8 | 1 | battery | u8 — encoded as raw ADC value; mV ≈ 2000 + battery × 10 |
| 9 | 1 | version_id | u8 — firmware version (101 in current firmware) |
| 10 | 1 | padding | u8 — always 0 |
| 11 | 1 | adc_data_id | u8 — 8-bit rolling sample counter (wraps at 256) |
| 12–27 | 16 | emg | 8 × int16 BE — raw EMG samples |
| 28–35 | 8 | fft | 4 × int16 BE — FFT bins 0–3 |
| 36–43 | 8 | quat | w, x, y, z — 4 × int16 BE |
| 44–49 | 6 | acc | ax, ay, az — 3 × int16 BE |
| 50–55 | 6 | euler | yaw, pitch, roll — 3 × int16 BE |
| 56–61 | 6 | mag | mx, my, mz — 3 × int16 BE |

Key differences from BLE GATT format:
- All integers are **big-endian** (GATT is little-endian)
- `unit_id` is the raw FICR device ID, not a CRC hash
- `adc_data_id` is an 8-bit counter (GATT tsrc_base is 32-bit)
- Battery is encoded as a raw ADC value (mV = 2000 + value × 10), not a percentage
- FFT bins are present in every packet (not available over BLE GATT)
- No format byte — packet type is inferred from `packet_type` field value

A PC-side serial adapter converts this stream to UF1 UDP. See Section 11.

---

## 10. Transport

### 10.1 UDP (primary)

One UF1 frame per UDP datagram. Default port: **26750**.

No fragmentation. Frames exceeding the UDP payload limit are undefined (in practice all current frames are well under 1500 bytes).

Optional discovery beacon (JSON) can be added later; not required for v2.

### 10.2 Serial (base station)

The base station serial stream (Section 9.2) is a legacy format. The base station firmware is a transparent bridge and is **not expected to output UF1**.

A host-side PC serial adapter converts the 0x4F+0xD5 serial stream to UF1 UDP on localhost, allowing all tools to consume base station data via the standard UDP path.

---

## 11. Known Adapter Implementations

### Android BLE Adapter

**Source:** `umyo-android` — `Uf1Codec.kt` + `DeviceSession.kt`

**Input:** BLE GATT notifications from uMyo firmware over custom characteristic

**Output:** UF1 UDP frames to configurable host IP, port 26750

**Behavior:**
- Dispatches on format byte (v2 firmware) or payload size (v1 firmware compatibility)
- Computes `device_id` as `CRC32(MAC_address_string)`
- Assigns stable monotonic `seq` per device using AtomicInteger
- Sets `t_us` from `SystemClock.elapsedRealtimeNanos() / 1000`
- Emits DEVICE_NAME frame once on connect and every ~5 seconds

**Known limitations:**
- `battery_pct` filled from GATT characteristic once per connect; not updated per-frame
- `sample_rate_hz` reported as negotiated rate from firmware; 0 if unknown
- S2 aux frames carry t_src_sample=0 (no counter available in raw aux payload)

### PC Serial Adapter

**Source:** `uf1-tools` (in progress)

**Input:** Base station USB serial at 921,600 baud (Section 9.2 format)

**Output:** UF1 UDP frames on localhost:26750

**Behavior:**
- Syncs on 0x4F 0xD5 byte sequence
- Parses nRF24 big-endian payload
- Converts battery ADC value to percentage for STATUS.battery_pct
- Maps raw RSSI register to STATUS.rssi_dbm using NRF52 calibration formula
- Emits STATUS + EMG_RAW + QUAT + IMU_6DOF + MAG_3 blocks per packet

---

## 12. Profiles and Rates (1 sensor target)

Assumptions: sampling ~1150 Hz, 8 samples per EMG frame → ~143.75 EMG frames/sec.

### S1 Profile — Low-MTU / PhoneOpt (mode = 0)

- EMG frame: STATUS + EMG_RAW @ 143.75 Hz → ~8.6 KB/s
- QUAT frame: STATUS + QUAT @ ~50 Hz → ~2.4 KB/s
- FFT computed on receiving end (not transmitted)
- MAG only on calibration screen (optional low rate)
- Total ≈ 10.8 KiB/s payload

### S2 Profile — Full Stream (mode = 1)

- EMG frame: STATUS + EMG_RAW @ 143.75 Hz → ~8.6 KB/s
- IMU frame: STATUS + IMU_6DOF @ ~50 Hz
- QUAT frame: STATUS + QUAT @ ~50 Hz → combined ~3.15 KB/s
- MAG frame: STATUS + MAG_3 @ ~10 Hz (calibration/debug)
- FFT4 frame: optional @ ~25 Hz
- Total ≈ 13.1 KiB/s payload (with MAG)

---

## 13. CRC32 Details

See Section 8. Unchanged from v1.

---

## 14. Testing Checklist

- Confirm adapter can sustain S2 profile without stalls on target BLE central hardware.
- Confirm `seq` increments monotonically and dropped frames are detectable.
- Confirm `t_src_sample` is monotonic and steps by 8 each EMG frame (S1) or 24 (S2).
- Confirm co-emitted QUAT and EMG frames from the same GATT notification carry matching `t_src_sample`.
- Confirm `mode` in STATUS matches actual active profile (0 for S1, 1 for S2).
- Confirm `battery_pct` reports 0–100 range (not 255) once firmware populates it.
- Confirm UI shows mode badge (S1/PhoneOpt vs S2/FullStream) deterministically.
- Confirm recordings can be replayed and time-aligned using `t_src_sample`.
- Confirm base station serial adapter syncs on 0x4F+0xD5 and emits valid UF1.
- Confirm all tools ignore 0xF1 DEBUG blocks without error.
