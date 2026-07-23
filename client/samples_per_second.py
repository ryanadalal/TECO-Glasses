import pandas as pd

def compute_fs(csv_path):
    df = pd.read_csv(csv_path)

    # parse timestamps
    df["time"] = pd.to_datetime(df["time"], format="%H:%M:%S.%f")

    # convert to seconds from start
    t = (df["time"] - df["time"].iloc[0]).dt.total_seconds()

    # compute sample intervals
    dt = t.diff().dropna()

    # statistics
    mean_dt = dt.mean()
    fs = 1.0 / mean_dt

    print(f"Samples: {len(df)}")
    print(f"Mean dt: {mean_dt:.6f} s")
    print(f"Estimated sampling rate (fs): {fs:.2f} Hz")

    return fs


# example usage
compute_fs("./recordings/25_1656/H.csv")