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

#BLE_ADDRESS = "F83153A9-8994-8BC6-CAD9-8C2365BAA7C9"
BLE_ADDRESS = "73:4C:69:84:C4:E4"
CHARACTERISTIC_UUID = "20a4a273-c214-4c18-b433-329f30ef7275"

# Plotting configuration
dataListH = []
dataListV = []
max_datapoints_to_display = 700
min_buffer_uV = 150
inamp_gain = 50
sample_rate = 256 # was 256
filters_h = digitalfilter.get_Biopotential_filter(order=4, cutoff=[1, 30], btype="bandpass", fs=sample_rate, output="sos")
filters_v = digitalfilter.get_Biopotential_filter(order=4, cutoff=[1, 30], btype="bandpass", fs=sample_rate, output="sos")
enable_filters = True
write_to_file = False
autoscale = False

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

key_map = {
    'a': 'LEFT',
    'd': 'RIGHT',
    'w': 'UP',
    'x': 'DOWN'
}

reference_hz = 35

fig, ax = plt.subplots()
lineH, = ax.plot([], [], label='Horizontal Channel', color='blue')
lineV, = ax.plot([], [], label='Vertical Channel', color='red')
line50, = ax.plot([], [], label=f'{reference_hz} Hz reference', color='green')

refH = 0
maxRefH = 10

paused = False

exit_event = threading.Event()

last_valid_timestamp = None

def init():
    lineH.set_data([], [])
    lineV.set_data([], [])
    line50.set_data([], [])
    ax.set_xlim(0, max_datapoints_to_display)
    ax.set_title("BioPotential Data from Open Earable")
    ax.set_ylabel("Voltage (µV)")
    ax.set_xlabel("Samples")
    ax.legend()
    return lineH, lineV,

def animate(frame):
    global dataListH
    global dataListV

    t = np.arange(len(dataListH)) / sample_rate
    sine = refH * np.sin(2 * np.pi * reference_hz * t)

    line50.set_data(range(1, len(sine) + 1), sine)

    # DataList is updated in the notification handler
    dataListH = dataListH[-max_datapoints_to_display:]  # Keep only the latest data points
    dataListV = dataListV[-max_datapoints_to_display:]  # Keep only the latest data points
    lineH.set_data(range(1, len(dataListH) + 1), dataListH)
    lineV.set_data(range(1, len(dataListV) + 1), dataListV)

    

    if dataListH or dataListV:
        all_vals = dataListH + dataListV
        min_val = min(all_vals)
        max_val = max(all_vals)
        buffer = 0.1 * (max_val - min_val) if max_val - min_val > min_buffer_uV else min_buffer_uV
        if autoscale:
            ax.set_ylim(min_val - buffer, max_val + buffer)
        else:
            ax.set_ylim(-min_buffer_uV, min_buffer_uV)
        freq_h = find_peak_frequency(dataListH, sample_rate)
        freq_v = find_peak_frequency(dataListV, sample_rate)

        if freq_h is not None:
            ax.set_title(
                f"H Peak: {freq_h:.1f} Hz | V Peak: {freq_v:.1f} Hz"
            )
    
    return lineH, lineV, line50

def notification_handler(sender, data):
    global dataListH
    global dataListV
    global enable_filters
    global sample_rate
    global last_valid_timestamp

    readings = struct.unpack('<4f', data)
    channelA = [readings[0], readings[2]]
    channelB = [readings[1], readings[3]]
    timestamp = datetime.now()

    if last_valid_timestamp is None:
        last_valid_timestamp = timestamp - timedelta(seconds=5 * 1/sample_rate)

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

        time_diff = (timestamp - last_valid_timestamp) / 2 # 2 is the number of data points being sent for each channel
        timestamp_for_float_value = last_valid_timestamp + time_diff * (i + 1)

        if write_to_file:
            recording_fileH.write(f"{timestamp_for_float_value.strftime('%H:%M:%S.%f')},{raw_h},{filtered_h}\n")
            recording_fileV.write(f"{timestamp_for_float_value.strftime('%H:%M:%S.%f')},{raw_v},{filtered_v}\n")

    # Update last_valid_timestamp to be the timestamp for the 5th float
    last_valid_timestamp = timestamp
def handle_key_press(event):
    global paused
    global refH

    if event.key == ' ':
        paused = not paused

        if paused:
            ani.event_source.stop()
            print("Paused")
        else:
            ani.event_source.start()
            print("Running")
    elif event.key == 'r':
        refH = 0 if refH != 0 else maxRefH
    elif event.key in ['a', 'x', 'd', 'w']:
        label = key_map[event.key]
        label_file.write(f"{datetime.now().strftime('%H:%M:%S.%f')},{label}\n")
        label_file.flush()
        print("Label:", label)
async def run_ble_client():
    async with BleakClient(BLE_ADDRESS) as client:
        await client.start_notify(CHARACTERISTIC_UUID, notification_handler)
        print("Connected and receiving data...")

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
    plt.close(fig)

def handle_close(evt):
    cleanup()
    sys.exit(0)

def find_peak_frequency(signal, fs):
    if len(signal) < fs:
        return None

    x = np.array(signal[-fs*4:])  # last 4 seconds
    x = x - np.mean(x)

    fft = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), d=1/fs)

    magnitude = np.abs(fft)

    peak_idx = np.argmax(magnitude[1:]) + 1
    return freqs[peak_idx]

"""
def handle_key_press(event):
    global cmd_pressed
    if event.key == 'g':
        insert_datapoint()

def handle_key_release(event):
    global cmd_pressed
    if event.key == 'cmd' or event.key == 'control':
        insert_datapoint()
"""
if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    fig.canvas.mpl_connect('close_event', handle_close)
    fig.canvas.mpl_connect('key_press_event', handle_key_press)
    #fig.canvas.mpl_connect('key_press_event', handle_key_press)
    #fig.canvas.mpl_connect('key_release_event', handle_key_release)
    
    threading.Thread(target=start_async_loop, daemon=True).start()

    ani = animation.FuncAnimation(fig, animate, init_func=init, interval=5, save_count=max_datapoints_to_display)
    plt.show()
