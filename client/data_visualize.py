import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

# -----------------------
# Load data
# -----------------------
session = "26_0928"

h = pd.read_csv(f"./recordings/{session}/H.csv")
v = pd.read_csv(f"./recordings/{session}/V.csv")
labels = pd.read_csv(f"./recordings/{session}/labels.csv")

# -----------------------
# Time parsing
# -----------------------
h["time"] = pd.to_datetime(h["time"], format="%H:%M:%S.%f")
v["time"] = pd.to_datetime(v["time"], format="%H:%M:%S.%f")

h["t"] = (h["time"] - h["time"].iloc[0]).dt.total_seconds()
v["t"] = (v["time"] - v["time"].iloc[0]).dt.total_seconds()

labels["time"] = pd.to_datetime(labels["time"], format="%H:%M:%S.%f")
labels["t"] = (labels["time"] - h["time"].iloc[0]).dt.total_seconds()

# -----------------------
# Plot
# -----------------------
fig, ax = plt.subplots(figsize=(12, 6))

h_line, = ax.plot(h["t"], h["filtered_data"], label="H (filtered)", color="blue")
v_line, = ax.plot(v["t"], v["filtered_data"], label="V (filtered)", color="red")

ax.set_title("EOG Signals (H + V) with Labels")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Amplitude (µV)")
ax.set_ylim(-250, 250)
ax.legend()
ax.grid(True)

# -----------------------
# Store annotation artists
# -----------------------
ann_lines = []
ann_texts = []

colors = {
    "LEFT": "green",
    "RIGHT": "orange",
    "UP": "purple",
    "DOWN": "black",
    "CENTER": "gray"
}

for _, row in labels.iterrows():
    t = row["t"]
    label = row["label"]

    ln = ax.axvline(
        x=t,
        color=colors.get(label, "gray"),
        linestyle="--",
        alpha=0.6
    )

    tx = ax.text(
        t,
        0,
        label,
        rotation=90,
        fontsize=8
    )

    ann_lines.append(ln)
    ann_texts.append(tx)

# -----------------------
# Toggle state
# -----------------------
H_annotations_visible = True
V_annotations_visible = True
C_annotations_visible = True

def on_key(event):
    global H_annotations_visible
    global V_annotations_visible
    global C_annotations_visible

    if event.key == "h":
        H_annotations_visible = not H_annotations_visible

        for i in range(len(ann_lines)):
            if ann_texts[i].get_text() == "LEFT" or ann_texts[i].get_text() == "RIGHT":
                ann_lines[i].set_visible(H_annotations_visible)
                ann_texts[i].set_visible(H_annotations_visible)
        h_line.set_visible(H_annotations_visible)

        fig.canvas.draw_idle()
    elif event.key == "v":
        V_annotations_visible = not V_annotations_visible

        for i in range(len(ann_lines)):
            if ann_texts[i].get_text() == "UP" or ann_texts[i].get_text() == "DOWN":
                ann_lines[i].set_visible(V_annotations_visible)
                ann_texts[i].set_visible(V_annotations_visible)
        v_line.set_visible(V_annotations_visible)

        fig.canvas.draw_idle()
    elif event.key == "c":
        C_annotations_visible = not C_annotations_visible

        for i in range(len(ann_lines)):
            if ann_texts[i].get_text() == "CENTER":
                ann_lines[i].set_visible(C_annotations_visible)
                ann_texts[i].set_visible(C_annotations_visible)

        fig.canvas.draw_idle()


# -----------------------
# Connect keyboard event
# -----------------------
fig.canvas.mpl_connect("key_press_event", on_key)

plt.tight_layout()
plt.show()