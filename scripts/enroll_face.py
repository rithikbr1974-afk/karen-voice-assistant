"""Enroll the user's face for face unlock (core/face.py). Opens the webcam in a window and takes ~30 face samples
while they turn their head a little. Saves only the face numbers to data/face_profile.npy (no photos).

    python scripts/enroll_face.py          enroll (replaces the old profile)
    python scripts/enroll_face.py --test   live check: shows the match score for whoever is in front of the camera
"""
import os
import sys
import time

import cv2
import numpy as np

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ".")
import yaml  # noqa: E402

from core.face import DET, PROFILE, REC, FaceLock  # noqa: E402

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
face_cfg = cfg.get("face", {})
camera = (cfg.get("explore") or {}).get("camera", 0)
if not (os.path.exists(DET) and os.path.exists(REC)):
    sys.exit("Face models missing: run python scripts/download_models.py")

STEPS = ["Look straight at the camera", "Turn your head a little LEFT", "Turn your head a little RIGHT",
         "Tilt your chin a little UP", "Tilt your chin a little DOWN", "Look straight again (smile if you like)"]
PER_STEP = 5
test = "--test" in sys.argv
lock = FaceLock(camera, face_cfg.get("threshold", 0.42))
if test and lock.profile is None:
    sys.exit("No face enrolled yet: run python scripts/enroll_face.py first.")
cam = cv2.VideoCapture(camera, cv2.CAP_DSHOW)
if not cam.isOpened():
    sys.exit("The camera didn't open (is Karen's camera look or another app using it?).")

feats, step, last, start = [], 0, 0.0, time.time()
print("Press Q in the camera window to quit.")
while True:
    ok, frame = cam.read()
    if not ok:
        continue
    view = frame.copy()
    feat, box = lock.embed(frame)
    if box is not None:
        x, y, w, h = map(int, box[:4])
        color = (0, 200, 0)
        if test:
            s = lock.score(feat)
            color = (0, 200, 0) if s >= lock.threshold else (0, 0, 255)
            cv2.putText(view, f"score {s:.2f} (unlocks at {lock.threshold:.2f})", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
    if not test:
        if step >= len(STEPS):
            break
        cv2.putText(view, f"{STEPS[step]}  ({len(feats) % PER_STEP}/{PER_STEP})", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 120, 200), 2)
        if feat is not None and time.time() - last > 0.35 and time.time() - start > 1.5:
            feats.append(feat)
            last = time.time()
            if len(feats) % PER_STEP == 0:
                step += 1
                start = time.time()  # a moment to move between steps
    else:
        cv2.putText(view, "TEST: green = unlocks", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 120, 200), 2)
    cv2.imshow("Karen face unlock", view)
    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
        break
cam.release()
cv2.destroyAllWindows()

if not test:
    if len(feats) < len(STEPS) * PER_STEP:
        sys.exit("Stopped early; nothing saved.")
    F = np.array(feats, dtype=np.float32)
    sims = F @ F.T
    np.fill_diagonal(sims, -1)
    own = sims.max(axis=1)  # each sample against the rest: how surely they match themselves
    os.makedirs("data", exist_ok=True)
    np.save(PROFILE, F)
    print(f"Saved {len(F)} face samples to {PROFILE}.")
    print(f"Self-match: lowest {own.min():.2f}, typical {np.median(own):.2f} (unlock threshold {lock.threshold:.2f}).")
    print("Now turn on Settings > Face unlock, or test with: python scripts/enroll_face.py --test")
