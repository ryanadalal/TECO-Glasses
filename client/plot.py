"""
OpenEarable 2 ExG Branch - EOG/EEG BLE Plotter (Dual Channel)
==============================================================
Firmware packet format (batched):
  Header (10 bytes):
    byte 0      : sensor_id   (uint8)
    byte 1      : size        (uint8)  — payload bytes = sizeof(SamplePair) * BATCH_SIZE = 36
    bytes 2-9   : batch_start_time (uint64 LE, microseconds)

  Payload (36 bytes = 4 x SamplePair):
    Each SamplePair (9 bytes, packed):
      float  ch0    (4B, µV)
      float  ch1    (4B, µV)
      uint8  dt_ms  (1B, ms offset from batch_start_time)

  Reconstruct absolute timestamp of sample i:
    t_i_us = batch_start_time + dt_ms[i] * 1000
"""

import asyncio
from bleak import BleakClient, BleakScanner
import struct
from collections import deque
from datetime import datetime
import threading
import time
import sys
import signal

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np

import digitalfilter

# ── BLE UUIDs ─────────────────────────────────────────────────────────────────
SENSOR_CONFIG_CHAR = "34c2e3be-34aa-11eb-adc1-0242ac120002"
SENSOR_DATA_CHAR   = "34c2e3bc-34aa-11eb-adc1-0242ac120002"
BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"

# ── Sensor config ──────────────────────────────────────────────────────────────
ID_EXG = 9

# Per-channel SPS per index (= ADC ODR / 2, two channels time-multiplexed):
# Index: 0      1      2      3     4      5      6     7
# SPS:   6.25   15.0   32.0   40.0  63.16  126.3  600   2400.0 # nominal sampling rates
SAMPLE_RATE_OPTIONS = [6.25, 15.0, 32.0, 40.0, 63.16, 126.3, 570.0, 2400.0] # measured sampling rates
SAMPLE_RATE_IDX       = 6
SAMPLE_RATE_PER_CH_HZ    = SAMPLE_RATE_OPTIONS[SAMPLE_RATE_IDX]
DATA_STREAMING        = 0x01

# ── Packet geometry (must match firmware constants) ────────────────────────────
HEADER_SIZE   = 10          # id(1) + size(1) + timestamp_us(8)
BATCH_SIZE    = 4           # ExG::BATCH_SIZE
SAMPLE_STRUCT = struct.Struct('<ffB')   # float ch0, float ch1, uint8 dt_ms
SAMPLE_BYTES  = SAMPLE_STRUCT.size     # 9 bytes
PAYLOAD_BYTES = SAMPLE_BYTES * BATCH_SIZE  # 36 bytes
PACKET_MIN    = HEADER_SIZE + PAYLOAD_BYTES  # 46 bytes minimum

# ── Display config ─────────────────────────────────────────────────────────────
DISPLAY_WINDOW_S     = 3          # seconds of history shown on screen
MAX_BUFFER_SAMPLES   = 3000      # hard cap so buffers don't grow forever
MAX_DISPLAY_SAMPLES  = int(SAMPLE_RATE_PER_CH_HZ * DISPLAY_WINDOW_S)
DISPLAY_RANGE_UV     = 200
AUTOSCALE            = False
WRITE_TO_FILE        = False

# display setting
paused = False

# ── Filtering config ────────────────────────────────────────────────────────────
# Live bandpass applied to each channel independently, matching the approach
# used in the older single-channel EOG script (digitalfilter.py).
ENABLE_FILTERS   = True
FILTER_ORDER     = 4
FILTER_CUTOFF_HZ = [0.1, 30]   # bandpass edges in Hz; must stay below fs/2
FILTER_BTYPE     = "bandpass"


# NOTE: at low sample rates (e.g. 6.25/15/32/40 SPS/ch) fs/2 can be lower than
# the upper cutoff above, which will make iirfilter raise an error. Guard here
# so filter construction fails loudly and clearly rather than silently.
_nyquist = SAMPLE_RATE_PER_CH_HZ / 2.0
if ENABLE_FILTERS and max(FILTER_CUTOFF_HZ) >= _nyquist:
    raise ValueError(
        f"FILTER_CUTOFF_HZ {FILTER_CUTOFF_HZ} invalid for fs={SAMPLE_RATE_PER_CH_HZ} Hz "
        f"(Nyquist={_nyquist} Hz). Lower the cutoff or raise SAMPLE_RATE_IDX."
    )

filters_ch0 = digitalfilter.get_Biopotential_filter(
    order=FILTER_ORDER, cutoff=FILTER_CUTOFF_HZ, btype=FILTER_BTYPE,
    fs=SAMPLE_RATE_PER_CH_HZ, output="sos"
)
filters_ch1 = digitalfilter.get_Biopotential_filter(
    order=FILTER_ORDER, cutoff=FILTER_CUTOFF_HZ, btype=FILTER_BTYPE,
    fs=SAMPLE_RATE_PER_CH_HZ, output="sos"
)

# ── Oscillation frequency diagnosis ────────────────────────────────────────────
# Finds the dominant frequency in the FILTERED signal — i.e. what's actually
# going to the graph — via FFT, so you can read off the interference frequency
# and set EXTRA_NOTCH_FREQS_HZ to exactly that value instead of guessing.
FFT_TARGET_WINDOW_S = 4  # desired FFT window length, in seconds — see cap below
# The caller (animate()) only ever hands find_peak_frequency the tail of the
# display buffer, which holds at most MAX_DISPLAY_SAMPLES samples
# (DISPLAY_WINDOW_S seconds' worth). Targeting more than that is pointless —
# the slice would silently be shorter than intended and the window duration
# implied by FFT_TARGET_WINDOW_S would be a lie. So cap the window to whatever
# is actually available instead of maintaining a second, separate buffer.
FFT_WINDOW_SAMPLES = max(64, min(int(SAMPLE_RATE_PER_CH_HZ * FFT_TARGET_WINDOW_S), MAX_DISPLAY_SAMPLES))
FFT_IGNORE_BELOW_HZ = 0.5   # skip DC / slow drift when looking for the peak

def find_peak_frequency(samples, fs, ignore_below_hz=FFT_IGNORE_BELOW_HZ):
    """Return (peak_freq_hz, peak_magnitude) for the dominant AC component in
    samples, or (None, None) if there isn't enough data yet."""
    if len(samples) < fs:  # need at least ~1 second to resolve low frequencies
        return None, None

    x = np.asarray(samples[-FFT_WINDOW_SAMPLES:], dtype=float)
    x = x - np.mean(x)  # remove DC offset

    fft = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fs)
    magnitude = np.abs(fft)

    valid = freqs >= ignore_below_hz
    if not np.any(valid):
        return None, None

    idx = np.argmax(magnitude[valid])
    peak_freq = freqs[valid][idx]
    peak_mag  = magnitude[valid][idx]
    return peak_freq, peak_mag

# ── Rolling sample-rate measurement ───────────────────────────────────────────
# Measured from the device's own per-sample clock — batch_start_time (µs,
# from the packet header) plus each sample's dt_ms offset — NOT from BLE
# notification arrival time. Arrival time is affected by the BLE connection
# interval, radio retransmits, and OS/Bleak scheduling, none of which reflect
# the ADC's actual sample clock. Using dt_ms also gives sub-batch precision
# (per-sample, not just per-packet), instead of only knowing "4 samples
# arrived sometime around this packet".
#
# Rate = (samples in window - 1) / (span of device-clock time covered by
# those samples), using a 3-second sliding window so the displayed rate is
# responsive but not jittery.
RATE_WINDOW_S   = 3.0
RATE_WINDOW_US  = int(RATE_WINDOW_S * 1e6)
_rate_samples   = deque()   # deque of device-clock timestamps (µs), oldest first
_rate_lock      = threading.Lock()
_current_sps    = 0.0       # updated by notification_handler, read by animate()

def _update_rate(sample_timestamps_us) -> None:
    """Record device-clock timestamps (µs) for newly arrived samples and
    recompute _current_sps from their span, using the device clock rather
    than BLE arrival wall-time."""
    global _current_sps
    if not sample_timestamps_us:
        return
    with _rate_lock:
        _rate_samples.extend(sample_timestamps_us)
        newest = _rate_samples[-1]
        cutoff = newest - RATE_WINDOW_US
        while _rate_samples and _rate_samples[0] < cutoff:
            _rate_samples.popleft()
        if len(_rate_samples) > 1:
            span_us = _rate_samples[-1] - _rate_samples[0]
            _current_sps = (len(_rate_samples) - 1) / (span_us / 1e6) if span_us > 0 else 0.0
        else:
            _current_sps = 0.0

# ── Shared data buffers ────────────────────────────────────────────────────────
# Each entry: (timestamp_us, ch0_uV, ch1_uV) — filtered values (or raw, if
# ENABLE_FILTERS is False), which is what gets plotted and recorded.
samples_lock = threading.Lock()
dataListCh0  = []
dataListCh1  = []
dataListTime = []   # absolute µs timestamps, used for both plotting and recording

exit_event = threading.Event()

battery_lock = threading.Lock()
battery_pct  = None   # None until first reading arrives

# ── Optional file recording ────────────────────────────────────────────────────
recording_file = None
if WRITE_TO_FILE:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    recording_file = open(f"./recordings/OpenEarableExG_{ts}.csv", 'w')
    recording_file.write("timestamp_us,ch0_raw_uV,ch1_raw_uV,ch0_filtered_uV,ch1_filtered_uV\n")

# ── Matplotlib setup ───────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 7), dpi=100)
lineCh0, = ax.plot([], [], label='Ch0 — AIN0/AIN1', color='steelblue', linewidth=0.8)
lineCh1, = ax.plot([], [], label='Ch1 — AIN0/AIN2', color='tomato',    linewidth=0.8)

battery_text = ax.text(
    0.98, 0.97, "Battery: --",
    transform=ax.transAxes,
    ha='right', va='top',
    fontsize=9,
    bbox=dict(boxstyle='round', facecolor='white', alpha=0.7, edgecolor='gray')
)

def init_plot():
    lineCh0.set_data([], [])
    lineCh1.set_data([], [])
    ax.set_xlim(-DISPLAY_WINDOW_S, 0)
    ax.set_ylim(-DISPLAY_RANGE_UV, DISPLAY_RANGE_UV)
    filt_tag = f"filtered {FILTER_CUTOFF_HZ[0]}-{FILTER_CUTOFF_HZ[1]}Hz" if ENABLE_FILTERS else "raw"
    ax.set_title(
        f"OpenEarable 2 ExG — Dual Channel ({filt_tag})  |  "
    )
    ax.set_ylabel("Voltage (µV)")
    ax.set_xlabel("Time (s ago)")
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)
    return lineCh0, lineCh1, battery_text


def animate(frame):
    with samples_lock:
        # Grab a generous tail; we'll trim to the exact time window below.
        t_us = dataListTime[-MAX_DISPLAY_SAMPLES:]
        ch0  = dataListCh0[-MAX_DISPLAY_SAMPLES:]
        ch1  = dataListCh1[-MAX_DISPLAY_SAMPLES:]

    if t_us:
        t = np.asarray(t_us, dtype=np.float64)
        t_rel = (t - t[-1]) / 1e6  # seconds relative to newest sample; 0 = now
        mask = t_rel >= -DISPLAY_WINDOW_S
        t_rel_disp = t_rel[mask]
        ch0_disp = np.asarray(ch0)[mask]
        ch1_disp = np.asarray(ch1)[mask]
    else:
        t_rel_disp, ch0_disp, ch1_disp = np.array([]), np.array([]), np.array([])

    lineCh0.set_data(t_rel_disp, ch0_disp)
    lineCh1.set_data(t_rel_disp, ch1_disp)

    # _current_sps counts individual samples (per channel), so it already
    # represents SPS/ch because each SamplePair is one ch0 + one ch1 reading.
    with _rate_lock:
        sps = _current_sps

    with battery_lock:
        batt = battery_pct

    # Peak frequency of the FILTERED signal — i.e. what's actually on the graph.
    # Uses the raw (untrimmed-by-time-window) tail so it still has ~4s of data
    # to work with even when DISPLAY_WINDOW_S is short.
    #
    # NOTE: the FFT frequency axis is derived directly from the "fs" we pass
    # in, so it needs to reflect the *actual* hardware rate, not the nominal
    # configured one — otherwise the reported peak frequency is scaled off by
    # however much the real SPS/ch drifts from SAMPLE_RATE_PER_CH_HZ. We use
    # the live measured rate (sps, computed above) and only fall back to the
    # nominal rate for the first ~RATE_WINDOW_S seconds, before any batches
    # have arrived and sps is still 0.
    effective_fs = sps if sps > 0 else SAMPLE_RATE_PER_CH_HZ
    peak0, _ = find_peak_frequency(ch0, effective_fs)
    peak1, _ = find_peak_frequency(ch1, effective_fs)
    peak_tag = ""
    if peak0 is not None and peak1 is not None:
        peak_tag = f"  |  plotted peak: Ch0 {peak0:.1f}Hz, Ch1 {peak1:.1f}Hz"

    filt_tag = f"filtered {FILTER_CUTOFF_HZ[0]}-{FILTER_CUTOFF_HZ[1]}Hz" if ENABLE_FILTERS else "raw"
    ax.set_title(
        f"OE2.ExG ({filt_tag})  |  "
        f"{sps:.1f} / {SAMPLE_RATE_PER_CH_HZ:.0f} SPS/ch{peak_tag}"
    )

    if batt is not None:
        # Simple color cue: green > 30%, orange 10-30%, red < 10%
        color = 'green' if batt > 30 else ('orange' if batt > 10 else 'red')
        battery_text.set_text(f"Battery: {batt}%")
        battery_text.set_color(color)
    else:
        battery_text.set_text("Battery: --")

    if AUTOSCALE:
        all_vals = list(ch0_disp) + list(ch1_disp)
        if all_vals:
            mn, mx = min(all_vals), max(all_vals)
            buf = max(0.1 * (mx - mn), DISPLAY_RANGE_UV)
            ax.set_ylim(mn - buf, mx + buf)

    return lineCh0, lineCh1,


# ── BLE notification handler ───────────────────────────────────────────────────
def notification_handler(sender, data: bytearray):
    """
    Parse a batched ExG packet and append all samples to the display buffers.

    Partial batches are possible (early flush on dt_ms overflow in firmware),
    so we derive actual sample count from the size field rather than assuming
    BATCH_SIZE samples are always present.
    """
    if len(data) < HEADER_SIZE:
        return

    sensor_id        = data[0]
    payload_size     = data[1]
    batch_start_us   = struct.unpack_from('<Q', data, 2)[0]

    if sensor_id != ID_EXG:
        return

    if payload_size % SAMPLE_BYTES != 0:
        print(f"[WARN] Unexpected payload size {payload_size}, not a multiple of {SAMPLE_BYTES}")
        return

    n_samples = payload_size // SAMPLE_BYTES

    # ── Partial-batch warning ──────────────────────────────────────────────────
    # Each packet should normally carry BATCH_SIZE sample pairs.  Fewer means
    # the firmware flushed early (dt_ms overflow) which is fine occasionally,
    # but seeing it constantly points to a timing or batching problem.
    if n_samples < BATCH_SIZE:
        print(
            f"[WARN] Partial batch: got {n_samples}/{BATCH_SIZE} sample pairs "
            f"(payload={payload_size}B, t={batch_start_us}µs)"
        )

    if len(data) < HEADER_SIZE + payload_size:
        print(f"[WARN] Packet too short: got {len(data)}, expected {HEADER_SIZE + payload_size}")
        return

    abs_timestamps = []  # device-clock (µs) timestamp of each sample in this batch

    with samples_lock:
        for i in range(n_samples):
            offset = HEADER_SIZE + i * SAMPLE_BYTES
            ch0_uV, ch1_uV, dt_ms = SAMPLE_STRUCT.unpack_from(data, offset)
            abs_us = batch_start_us + dt_ms * 1000
            abs_timestamps.append(abs_us)

            # Apply the live biopotential filter chain per channel, sample-by-
            # sample, so state carries correctly across BLE packets — same
            # approach as the original single-channel script.
            if ENABLE_FILTERS:
                ch0_out = filters_ch0(ch0_uV)
                ch1_out = filters_ch1(ch1_uV)
            else:
                ch0_out = ch0_uV
                ch1_out = ch1_uV

            dataListCh0.append(ch0_out)
            dataListCh1.append(ch1_out)
            dataListTime.append(abs_us)

            if WRITE_TO_FILE and recording_file:
                recording_file.write(f"{abs_us},{ch0_uV},{ch1_uV},{ch0_out},{ch1_out}\n")

        # Bound buffer growth so a long-running session doesn't eat memory.
        # (Display always uses the last MAX_DISPLAY_SAMPLES anyway, so this
        # cap just needs to stay comfortably above that.)
        if len(dataListTime) > MAX_BUFFER_SAMPLES:
            overflow = len(dataListTime) - MAX_BUFFER_SAMPLES
            del dataListCh0[:overflow]
            del dataListCh1[:overflow]
            del dataListTime[:overflow]

    # Update rolling sample rate *outside* the data lock — it has its own lock.
    # abs_timestamps holds each sample's device-clock time (one entry per
    # SamplePair = one reading per channel, both channels advance in
    # lockstep), so the resulting rate is directly SPS/ch.
    _update_rate(abs_timestamps)

    if WRITE_TO_FILE and recording_file:
        recording_file.flush()

def battery_notification_handler(sender, data: bytearray):
    """Battery Level characteristic: single byte, 0-100 = percent."""
    global battery_pct
    if len(data) < 1:
        return
    with battery_lock:
        battery_pct = data[0]

# ── BLE async loop ─────────────────────────────────────────────────────────────
async def run_ble():
    print("Scanning for OpenEarable device...")
    devices = await BleakScanner.discover(timeout=2.0)
    target  = next((d for d in devices if (d.name or "").startswith("OpenEarable")), None)

    retry = 1
    while target is None:
        print("No OpenEarable device found.")
        print(f"Re-scanning for OpenEarable device... {retry}")
        retry += 1
        devices = await BleakScanner.discover(timeout=2.0)
        target  = next((d for d in devices if (d.name or "").startswith("OpenEarable")), None)
        if retry > 20:
            print("No OpenEarable device found after 20 attempts. Exiting.")
            exit_event.set()
            return

    print(f"Found: {target.name}  [{target.address}]")

    async with BleakClient(target.address) as client:
        print(f"Connected. Streaming ExG at {SAMPLE_RATE_PER_CH_HZ:.0f} SPS/ch "
              f"(rate_idx={SAMPLE_RATE_IDX}, batch={BATCH_SIZE})...")

        await client.start_notify(SENSOR_DATA_CHAR, notification_handler)
        try:
            initial_batt = await client.read_gatt_char(BATTERY_LEVEL_CHAR)
            battery_notification_handler(None, initial_batt)
        except Exception as e:
            print(f"[WARN] Could not read initial battery level: {e}")
        await client.start_notify(BATTERY_LEVEL_CHAR, battery_notification_handler)

        config_packet = bytes([ID_EXG, SAMPLE_RATE_IDX, DATA_STREAMING])
        await client.write_gatt_char(SENSOR_CONFIG_CHAR, config_packet, response=True)
        print(f"Config sent: {config_packet.hex()}")

        while not exit_event.is_set():
            await asyncio.sleep(0.5)

        stop_packet = bytes([ID_EXG, SAMPLE_RATE_IDX, 0x00])
        try:
            await client.write_gatt_char(SENSOR_CONFIG_CHAR, stop_packet, response=True)
        except Exception:
            pass
        print("ExG stream stopped.")


def start_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_ble())


# ── Cleanup ────────────────────────────────────────────────────────────────────
def cleanup(*args):
    exit_event.set()
    if WRITE_TO_FILE and recording_file:
        recording_file.close()
    plt.close(fig)


def handle_close(evt):
    cleanup()
    sys.exit(0)

def handle_key_press(event):
    global paused
    if event.key == ' ':
        paused = not paused

        if paused:
            ani.event_source.stop()
        else:
            ani.event_source.start()

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    fig.canvas.mpl_connect('close_event', handle_close)
    fig.canvas.mpl_connect('key_press_event', handle_key_press)

    threading.Thread(target=start_async_loop, daemon=True).start()

    ani = animation.FuncAnimation(
        fig, animate, init_func=init_plot,
        interval=20,
        save_count=MAX_DISPLAY_SAMPLES,
        blit=False   # title text update requires blit=False
    )
    plt.show()