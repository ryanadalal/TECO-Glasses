from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import ConfusionMatrixDisplay, accuracy_score, confusion_matrix
import matplotlib.pyplot as plt
from features import build_dataset


session = "26_0928"
random_state = 2

# -----------------------------
# BUILD DATASET
# -----------------------------
X, y, feature_names = build_dataset(
    f"./recordings/{session}/H.csv",
    f"./recordings/{session}/V.csv",
    f"./recordings/{session}/labels.csv",
    start_delay=25,
    end_delay=15,
    window_pre=0.2,
    window_post=0.8
)

print("Dataset size:", X.shape)

# -----------------------------
# SPLIT
# -----------------------------
X_train, X_test, y_train, y_test = train_test_split(
    X, y,
    test_size=0.25,
    random_state=random_state
)

# -----------------------------
# MODEL
# -----------------------------
model = RandomForestClassifier(
    n_estimators=300,
    max_depth=10,
    random_state=random_state
)

model.fit(X_train, y_train)

# -----------------------------
# EVALUATION
# -----------------------------
y_pred = model.predict(X_test)

print("accuracy:", accuracy_score(y_test, y_pred))

ConfusionMatrixDisplay.from_predictions(
    y_test,
    y_pred,
    cmap="Blues"
)

plt.show()