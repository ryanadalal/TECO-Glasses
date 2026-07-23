import pandas as pd
import matplotlib.pyplot as plt

session = "26_0928"

window_pre = 1
window_post = 2

# -----------------------
# Load data
# -----------------------

h = pd.read_csv(f"./recordings/{session}/H.csv")
v = pd.read_csv(f"./recordings/{session}/V.csv")
labels = pd.read_csv(f"./recordings/{session}/labels.csv")

# -----------------------
# Parse times
# -----------------------

h["time"] = pd.to_datetime(h["time"], format="%H:%M:%S.%f")
v["time"] = pd.to_datetime(v["time"], format="%H:%M:%S.%f")
labels["time"] = pd.to_datetime(labels["time"], format="%H:%M:%S.%f")

h["t"] = (h["time"] - h["time"].iloc[0]).dt.total_seconds()
v["t"] = (v["time"] - h["time"].iloc[0]).dt.total_seconds()
labels["t"] = (labels["time"] - h["time"].iloc[0]).dt.total_seconds()

current = 0

fig, ax = plt.subplots(figsize=(10, 5))


def draw_window(index):
    ax.clear()

    row = labels.iloc[index]
    t0 = row["t"]
    label = row["label"]

    h_win = h[(h["t"] >= t0 - window_pre) &
              (h["t"] <= t0 + window_post)]

    v_win = v[(v["t"] >= t0 - window_pre) &
              (v["t"] <= t0 + window_post)]

    ax.plot(
        h_win["t"],
        h_win["filtered_data"],
        label="Horizontal",
        linewidth=2
    )

    ax.plot(
        v_win["t"],
        v_win["filtered_data"],
        label="Vertical",
        linewidth=2
    )

    # Label timestamp
    ax.axvline(
        t0,
        color="red",
        linestyle="--",
        linewidth=2,
        label=f"Label: {label}"
    )

    # Window boundaries
    ax.axvline(t0 - window_pre, color="gray", linestyle=":")
    ax.axvline(t0 + window_post, color="gray", linestyle=":")

    ax.set_title(
        f"Window {index+1}/{len(labels)}   Label = {label}"
    )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Filtered Signal")
    ax.grid(True)
    ax.legend()

    fig.canvas.draw_idle()


def on_key(event):
    global current

    if event.key == " ":
        current = (current + 1) % len(labels)
        draw_window(current)

    elif event.key == "left":
        current = (current - 1) % len(labels)
        draw_window(current)

    elif event.key == "right":
        current = (current + 1) % len(labels)
        draw_window(current)


fig.canvas.mpl_connect("key_press_event", on_key)

draw_window(current)

plt.show()