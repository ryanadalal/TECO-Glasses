import pandas as pd
import numpy as np

# -----------------------------
# YOUR FEATURE FUNCTIONS (unchanged)
# -----------------------------
from scipy.stats import skew, kurtosis

def extract_channel_features(signal, time):
    signal = np.asarray(signal)
    time = np.asarray(time)

    velocity = np.gradient(signal, time)
    peak_idx = np.argmax(np.abs(signal))

    return {
        'mean': np.mean(signal),
        'max': np.max(signal),
        'min': np.min(signal),
        'std': np.std(signal),
        'rms': np.sqrt(np.mean(signal**2)),
        'ptp': np.ptp(signal),
        'energy': np.sum(signal**2),
        'skew': skew(signal),
        'kurtosis': kurtosis(signal),
        'max_slope': np.max(velocity),
        'min_slope': np.min(velocity),
        'max_abs_slope': np.max(np.abs(velocity)),
        'time_to_peak': time[peak_idx] - time[0],
        'auc': np.trapezoid(signal, time),
    }


def extract_eog_features_from_window(hsig, vsig, htime, vtime):
    h_features = extract_channel_features(hsig, htime)
    v_features = extract_channel_features(vsig, vtime)

    features = {
        **{f'h_{k}': v for k, v in h_features.items()},
        **{f'v_{k}': v for k, v in v_features.items()},
        'h_peak': np.max(np.abs(hsig)),
        'v_peak': np.max(np.abs(vsig)),
    }

    features['correlation'] = np.corrcoef(hsig, vsig)[0, 1] if len(hsig) > 1 and len(vsig) > 1 else 0.0

    features['peak_ratio'] = (
        features['h_peak'] / features['v_peak']
        if features['v_peak'] != 0 else 0.0
    )

    return features


# -----------------------------
# BUILD DATASET FROM SESSION
# -----------------------------
def build_dataset(h_csv, v_csv, label_csv,
                  window_pre=0.2, window_post=0.6, start_delay=10, end_delay=10):
    
    h = pd.read_csv(h_csv)
    v = pd.read_csv(v_csv)
    labels = pd.read_csv(label_csv)

    h["time"] = pd.to_datetime(h["time"], format="%H:%M:%S.%f")
    v["time"] = pd.to_datetime(v["time"], format="%H:%M:%S.%f")
    labels["time"] = pd.to_datetime(labels["time"], format="%H:%M:%S.%f")

    # convert to seconds
    h["t"] = (h["time"] - h["time"].iloc[0]).dt.total_seconds()
    v["t"] = (v["time"] - h["time"].iloc[0]).dt.total_seconds()
    labels["t"] = (labels["time"] - h["time"].iloc[0]).dt.total_seconds()

    # -------------------------
    # ALIGN TIME BASE
    # -------------------------
    start_time = max(h["t"].min(), v["t"].min(), labels["t"].min())
    end_time = min(h["t"].max(), v["t"].max(), labels["t"].max())

    h = h[(h["t"] >= start_time) & (h["t"] <= end_time)]
    v = v[(v["t"] >= start_time) & (v["t"] <= end_time)]
    labels = labels[(labels["t"] >= start_time) & (labels["t"] <= end_time)]

    # -------------------------
    # REMOVE EDGE EFFECTS
    # -------------------------
    h = h[(h["t"] > start_time + start_delay) & (h["t"] < end_time - end_delay)]
    v = v[(v["t"] > start_time + start_delay) & (v["t"] < end_time - end_delay)]
    labels = labels[(labels["t"] > start_time + start_delay) & (labels["t"] < end_time - end_delay)]

    X, y = [], []

    for _, row in labels.iterrows():
        t0 = row['t']
        label = row['label']

        h_win = h[(h['t'] >= t0 - window_pre) & (h['t'] <= t0 + window_post)]
        v_win = v[(v['t'] >= t0 - window_pre) & (v['t'] <= t0 + window_post)]

        if len(h_win) < 10 or len(v_win) < 10:
            continue

        feats = extract_eog_features_from_window(
            h_win['filtered_data'].values,
            v_win['filtered_data'].values,
            h_win['t'].values,
            v_win['t'].values
        )

        X.append(list(feats.values()))
        y.append(label)

    feature_names = list(feats.keys())

    return np.array(X), np.array(y), feature_names



