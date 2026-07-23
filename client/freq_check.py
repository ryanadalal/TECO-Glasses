import asyncio
from bleak import BleakClient
import struct
from datetime import datetime
import time

BLE_ADDRESS = "73:4C:69:84:C4:E4"
CHARACTERISTIC_UUID = "20a4a273-c214-4c18-b433-329f30ef7275"

# -----------------------------
# Sampling rate estimation
# -----------------------------
timestamps = []
window_seconds = 5  # sliding window

def update_rate():
    global timestamps

    now = time.time()
    timestamps.append(now)

    # keep only last N seconds
    timestamps = [t for t in timestamps if now - t <= window_seconds]

    if len(timestamps) < 2:
        return None

    duration = timestamps[-1] - timestamps[0]
    if duration <= 0:
        return None

    fs = (len(timestamps) - 1) / duration
    return fs


# -----------------------------
# BLE callback
# -----------------------------
def notification_handler(sender, data):
    readings = struct.unpack('<4f', data)

    fs = update_rate()

    if fs is not None:
        print(f"Estimated sampling rate: {fs:.2f} Hz", end="\r")


# -----------------------------
# BLE loop
# -----------------------------
async def run():
    async with BleakClient(BLE_ADDRESS) as client:
        await client.start_notify(CHARACTERISTIC_UUID, notification_handler)

        print("Connected. Measuring incoming frequency...")

        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run())