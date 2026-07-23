import asyncio
from bleak import BleakClient
import struct
from datetime import datetime, timedelta
import threading
import digitalfilter
import sys
import os
import signal

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np
import random

# BLE Configuration
BLE_ADDRESS = "73:4C:69:84:C4:E4"
CHARACTERISTIC_UUID = "20a4a273-c214-4c18-b433-329f30ef7275"

# Data Recording Configuration
dataListH = []
dataListV = []
inamp_gain = 50
sample_rate = 256
filters_h = digitalfilter.get_Biopotential_filter(order=4, cutoff=[1, 30], btype="bandpass", fs=sample_rate, output="sos")
filters_v = digitalfilter.get_Biopotential_filter(order=4, cutoff=[1, 30], btype="bandpass", fs=sample_rate, output="sos")
enable_filters = True
write_to_file = True

session_id = datetime.now().strftime("%d_%H%M")
session_dir = os.path.join("recordings", session_id)

if write_to_file:
    os.makedirs(session_dir, exist_ok=False)
    recording_fileH = open(os.path.join(session_dir, "H.csv"), "w")
    recording_fileH.write("time,raw_data,filtered_data\n")

    recording_fileV = open(os.path.join(session_dir, "V.csv"), "w")
    recording_fileV.write("time,raw_data,filtered_data\n")

    label_file = open(os.path.join(session_dir, "labels.csv"), "w")
    label_file.write("time,label\n")

# -------------------------------------------------------------
# FULL SCREEN & CORNER-TO-CORNER UI CONFIGURATION
# -------------------------------------------------------------
fig, ax = plt.subplots()

manager = plt.get_current_fig_manager()
try:
    manager.full_screen_toggle()
except AttributeError:
    pass

fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)
ax.set_xlim(-1.0, 1.0)
ax.set_ylim(-1.0, 1.0)
ax.axis('off')
fig.patch.set_facecolor('black')
ax.set_facecolor('black')

# Visual elements
center_dot, = ax.plot([0], [0], 'wo', markersize=14)   # White dot — always visible
target_dot,  = ax.plot([], [], 'ro', markersize=24)     # Red dot — directional targets
overlay_text = ax.text(0, 0.25, '', color='white', fontsize=36,
                       ha='center', va='center', fontweight='bold')
countdown_text = ax.text(0, -0.15, '', color='white', fontsize=96,
                         ha='center', va='center', fontweight='bold')

EDGE_VAL = 0.98

TARGET_POSITIONS = {
    'LEFT':   (-EDGE_VAL, 0),
    'RIGHT':  ( EDGE_VAL, 0),
    'UP':     (0,  EDGE_VAL),
    'DOWN':   (0, -EDGE_VAL),
    'CENTER': (0,  0),
}

DIRECTION_POOL = ['LEFT', 'RIGHT', 'UP', 'DOWN', 'CENTER']

# Frame constants (at ~60fps via interval=16ms)
FRAMES_PER_SEC = 60
COUNTDOWN_SECS = 3

class TrackingState:
    def __init__(self):
        self.current_state = 'WAITING'   # WAITING → COUNTDOWN → OFF → targets
        self.frames_in_state = 0
        self.hold_frames = 60            # ~750ms on target
        self.off_frames  = 120            # ~1250ms blank between targets
        self.connected   = False         # set True by BLE thread on first data

state = TrackingState()
exit_event = threading.Event()
last_valid_timestamp = None

def init():
    target_dot.set_data([], [])
    overlay_text.set_text('')
    countdown_text.set_text('')
    return center_dot, target_dot, overlay_text, countdown_text

def animate(frame):
    global state

    state.frames_in_state += 1

    # ── WAITING: show message until BLE data arrives ──────────────────────────
    if state.current_state == 'WAITING':
        overlay_text.set_text('Waiting for connection...')
        countdown_text.set_text('')
        target_dot.set_data([], [])
        if state.connected:
            state.current_state = 'COUNTDOWN'
            state.frames_in_state = 0

    # ── COUNTDOWN: 3 … 2 … 1 … ───────────────────────────────────────────────
    elif state.current_state == 'COUNTDOWN':
        total_frames = COUNTDOWN_SECS * FRAMES_PER_SEC
        elapsed = state.frames_in_state
        remaining_sec = COUNTDOWN_SECS - int(elapsed / FRAMES_PER_SEC)
        overlay_text.set_text('Starting in')
        countdown_text.set_text(str(max(remaining_sec, 1)))
        target_dot.set_data([], [])

        if elapsed >= total_frames:
            overlay_text.set_text('')
            countdown_text.set_text('')
            state.current_state = 'OFF'
            state.frames_in_state = 0

    # ── OFF: blank gap between targets ───────────────────────────────────────
    elif state.current_state == 'OFF':
        target_dot.set_data([], [])
        if state.frames_in_state >= state.off_frames:
            state.current_state = random.choice(DIRECTION_POOL)
            state.frames_in_state = 0
            now_str = datetime.now().strftime('%H:%M:%S.%f')
            if write_to_file:
                label_file.write(f"{now_str},{state.current_state}\n")
                label_file.flush()
            print(f"[{now_str}] Jumped to: {state.current_state}")

    # ── TARGET (including CENTER): show red dot ───────────────────────────────
    elif state.current_state in DIRECTION_POOL:
        x_pos, y_pos = TARGET_POSITIONS[state.current_state]
        target_dot.set_data([x_pos], [y_pos])
        if state.frames_in_state >= state.hold_frames:
            state.current_state = 'OFF'
            state.frames_in_state = 0

    return center_dot, target_dot, overlay_text, countdown_text

def notification_handler(sender, data):
    global dataListH, dataListV, enable_filters, sample_rate, last_valid_timestamp

    # Signal BLE connected on first packet
    if not state.connected:
        state.connected = True
        print("BLE connected — starting countdown.")

    readings = struct.unpack('<4f', data)
    channelA = [readings[0], readings[2]]
    channelB = [readings[1], readings[3]]
    timestamp = datetime.now()

    if last_valid_timestamp is None:
        last_valid_timestamp = timestamp - timedelta(seconds=2 * 1/sample_rate)

    for i, (h, v) in enumerate(zip(channelA, channelB)):
        filtered_h = filters_h(h)
        filtered_v = filters_v(v)

        filtered_h = (filtered_h / inamp_gain) * 1e6
        filtered_v = (filtered_v / inamp_gain) * 1e6
        raw_h = (h / inamp_gain) * 1e6
        raw_v = (v / inamp_gain) * 1e6

        if enable_filters:
            dataListH.append(filtered_h)
            dataListV.append(filtered_v)
        else:
            dataListH.append(raw_h)
            dataListV.append(raw_v)

        time_diff = (timestamp - last_valid_timestamp) / 2
        timestamp_for_float_value = last_valid_timestamp + time_diff * (i + 1)

        if write_to_file:
            recording_fileH.write(f"{timestamp_for_float_value.strftime('%H:%M:%S.%f')},{raw_h},{filtered_h}\n")
            recording_fileV.write(f"{timestamp_for_float_value.strftime('%H:%M:%S.%f')},{raw_v},{filtered_v}\n")

    last_valid_timestamp = timestamp

async def run_ble_client():
    async with BleakClient(BLE_ADDRESS) as client:
        await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
        print("BLE client running, waiting for notifications...")
        while not exit_event.is_set():
            await asyncio.sleep(1)

def start_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_ble_client())

def cleanup(*args):
    exit_event.set()
    if write_to_file:
        recording_fileH.close()
        recording_fileV.close()
        label_file.close()
    plt.close(fig)

def handle_close(evt):
    cleanup()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    fig.canvas.mpl_connect('close_event', handle_close)
    threading.Thread(target=start_async_loop, daemon=True).start()

    ani = animation.FuncAnimation(fig, animate, init_func=init, interval=16, blit=True)
    plt.show()