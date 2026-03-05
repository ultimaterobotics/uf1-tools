uDevices Unified Frame v1 (UF1) Spec

Decisions:
- UDP default port: 26750
- CRC32: optional (off by default in tools)
- Required profile: Phone Mode (Optimized) [Profile A]
- Advanced profile: Full Stream (Profile B) (not required for V1)

1) Goals

One binary frame format for phone mode, bridge, Windows tools, and future dongles

Supports “profiles” by including different blocks and rates

Makes timing robust for gesture training (source counter + rx time)

2) Endianness

All multi-byte fields are little-endian.

3) Frame header (24 bytes)
Field	Size	Type	Notes
magic	2	u8[2]	0x55 0x44 (“UD”)
version	1	u8	1
hdr_len	1	u8	24
frame_len	2	u16	total bytes including header (+ CRC if present)
flags	2	u16	see below
device_id	4	u32	uMyo unit ID
stream_id	1	u8	0 for now
reserved	1	u8	0
seq	4	u32	increments per frame per device
t_us	6	u48	monotonic rx_time in microseconds at frame creator hop
Flags (u16)

bit0: TIME_SRC_PRESENT (STATUS contains valid t_src_sample)

bit1: TIME_US_IS_RX (always 1 in v1)

bit2: CRC32_PRESENT (CRC32 appended)

others reserved 0

t_us wrap: 48-bit µs wraps after ~8.9 years.

4) Payload blocks (TLV)

Repeated until end (or before CRC):

type u8

len u16 (value length only)

value bytes[len]

Block type IDs (v1 set)

0x01 EMG_RAW

0x02 EMG_FFT4

0x03 IMU_6DOF

0x04 MAG_3

0x05 QUAT

0x06 STATUS

(Additional types can be added later; parsers ignore unknown types using len.)

5) Block layouts (v1)
0x06 STATUS (len = 10)
Offset	Size	Field	Type	Notes
0	4	t_src_sample	u32	sample counter of first EMG sample in the accompanying EMG_RAW frame
4	2	sample_rate_hz	u16	0 = default/unknown
6	1	battery_pct	u8	0–100, 255 unknown
7	1	rssi_dbm	i8	-128 unknown
8	1	mode	u8	0 PhoneOpt, 1 FullStream, 2 Other
9	1	status_flags	u8	bit0 cal_ok, bit1 imu_ok, bit2 mag_ok

If t_src_sample is valid, set header flag TIME_SRC_PRESENT.

0x01 EMG_RAW

Header + samples.

Offset	Size	Field	Type	Notes
0	1	channel_count	u8	v1: 1
1	1	samples_per_ch	u8	typically 8
2	1	sample_format	u8	1 = int16
3	1	reserved	u8	0
4	2*N	samples	int16[]	N = channel_count*samples_per_ch

Typical v1: 1 ch, 8 samples → len = 4 + 16 = 20 bytes.

0x05 QUAT (len = 8)

4× int16 (scaled) in order:

w, x, y, z (int16 each)

0x03 IMU_6DOF (len = 12)

6× int16:

ax, ay, az, gx, gy, gz

(Units/scaling documented elsewhere; v1 just defines byte layout.)

0x02 EMG_FFT4 (len = 8)

4× int16:

bin0, bin1, bin2, bin3

0x04 MAG_3 (len = 6)

3× int16:

mx, my, mz

6) CRC32 (optional)

If header flag CRC32_PRESENT is set:

append crc32 u32 at end of frame

CRC computed over all bytes from magic through last TLV (excluding crc field)

On CRC fail: drop frame and count loss (do not “repair”).

7) Profiles and rates (1 sensor target)

Assumptions:

sampling ~1150 Hz

8 samples per EMG frame → 143.75 EMG frames/sec

Profile A: Phone Mode (Optimized) — default

EMG frame: STATUS + EMG_RAW @ 143.75 Hz → 60 B/frame → ~8.6 KB/s

QUAT frame: STATUS + QUAT @ 50 Hz → 48 B/frame → ~2.4 KB/s

FFT computed on phone UI (default)

MAG only on calibration screen (optional low rate)

Total ≈ 10.8 KiB/s payload.

Profile B: Full Stream (High Bandwidth) — advanced

EMG frame: STATUS + EMG_RAW @ 143.75 Hz → ~8.6 KB/s

IMU+QUAT frame: STATUS + IMU_6DOF + QUAT @ 50 Hz → 63 B/frame → ~3.15 KB/s

FFT4 frame optional @ 25 Hz → ~1.2 KB/s

MAG frame calibration/debug only @ 10 Hz → ~0.46 KB/s

Total ≈ 13.1 KiB/s payload (with MAG), typically less.

8) Bridge transport v1

UDP datagram = exactly one UF1 frame

PC receiver listens on fixed port (choose later)

Optional discovery beacon (JSON) can be added later; not required for v1

9) Testing checklist (so you can validate reality fast)

Confirm phone can sustain Profile A without stalls.

Confirm seq increments and dropped frames are detectable.

Confirm t_src_sample is monotonic and steps by 8 each EMG frame.

Confirm UI shows mode badge (PhoneOpt vs FullStream) deterministically.

Confirm recordings can be replayed and time-aligned using t_src_sample.