"""Face unlock (JARVIS's face auth, rebuilt): OpenCV's YuNet finds the face, SFace turns it into 128 numbers, and
those are compared with the user's enrolled ones (data/face_profile.npy, from scripts/enroll_face.py). Local, CPU only;
no photos of him are kept (only a stranger's, for the intruder alert, in data/intruders). It keeps other people from using Karen while he's away. It is not strong security: a good
photo of him may pass, and it can't unlock Windows (no Windows Hello camera on this laptop)."""
import os
import time

import cv2
import numpy as np

DET = "models/face_detection_yunet.onnx"
REC = "models/face_recognition_sface.onnx"
PROFILE = "data/face_profile.npy"


class FaceLock:
    def __init__(self, camera=0, threshold=0.42):
        self.camera, self.threshold = camera, threshold
        self.det = cv2.FaceDetectorYN.create(DET, "", (320, 320), 0.8)
        self.rec = cv2.FaceRecognizerSF.create(REC, "")
        self.profile = np.load(PROFILE) if os.path.exists(PROFILE) else None

    @staticmethod
    def ready():
        return all(map(os.path.exists, (DET, REC, PROFILE)))

    def embed(self, frame):
        """(unit-length feature, box) of the largest face in a BGR frame, or (None, None)."""
        h, w = frame.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(frame)
        if faces is None or not len(faces):
            return None, None
        face = max(faces, key=lambda f: f[2] * f[3])
        feat = self.rec.feature(self.rec.alignCrop(frame, face)).flatten()
        return feat / np.linalg.norm(feat), face

    def score(self, feat):
        """Cosine similarity to the closest enrolled face (same person is usually 0.5-0.8, others below 0.35)."""
        return float(np.max(self.profile @ feat))

    def frames(self, seconds):
        """Webcam frames for `seconds`, skipping the dark ones the camera gives while it starts."""
        cam = cv2.VideoCapture(self.camera, cv2.CAP_DSHOW)
        try:
            if not cam.isOpened():
                return
            end, n = time.time() + seconds, 0
            while time.time() < end:
                ok, frame = cam.read()
                if not ok:
                    continue
                n += 1
                if n > 3 and frame.mean() > 15:
                    yield frame
        finally:
            cam.release()

    def check(self, seconds=8, need=2):
        """Look for him: (True, best score, "match") once `need` frames in a row match.
        Otherwise the reason: "camera" (no picture), "no face", or "not him"."""
        self.stranger = None  # (frame, score) of the clearest face that wasn't his: the intruder alert's photo
        if self.profile is None:
            return False, 0.0, "no profile"
        got = seen = hits = 0
        best = 0.0
        for frame in self.frames(seconds):
            got += 1
            feat, box = self.embed(frame)
            if feat is None:
                hits = 0
                continue
            seen += 1
            s = self.score(feat)
            best = max(best, s)
            hits = hits + 1 if s >= self.threshold else 0
            if s < self.threshold and (self.stranger is None or box[-1] > self.stranger[2]):  # box[-1]: detector confidence
                self.stranger = (frame, s, box[-1])
            if hits >= need:
                return True, best, "match"
        return False, best, "camera" if not got else "no face" if not seen else "not him"


def intruder_alert(frame, score, asked, log, folder="data/intruders", keep=20):
    """Someone who isn't the user tried voice Karen: save their photo and send it to his Telegram (Karen's own bot,
    the account paired in data/remote.json). Runs in a thread; sending never touches Remote's polling."""
    import datetime
    import json
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now()
    path = os.path.join(folder, f"{now:%Y-%m-%d_%H-%M-%S}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    for old in sorted(os.listdir(folder))[:-keep]:  # names sort by time
        os.remove(os.path.join(folder, old))
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        owners = json.load(open("data/remote.json", encoding="utf-8")).get("telegram", [])
    except (OSError, ValueError):
        owners = []
    if not token or not owners:
        log("face", f"intruder photo saved to {path} (no Telegram set up to send it)")
        return
    from core.telegram import Telegram
    said = f'\nThey said: "{asked[:200]}"' if asked else ""
    caption = (f"🚨 Someone who isn't you tried to use Karen on your PC at {now:%I:%M %p} "
               f"(face match {score:.2f}; you score ~0.9).{said}\nKaren stayed locked.")
    try:
        bot = Telegram(token)
        for chat in owners:  # a private chat's id is the user's id
            bot.send_file(chat, path, caption)
        log("face", f"intruder alert sent to Telegram ({path})")
    except Exception as e:
        log("error", f"intruder alert: {type(e).__name__}"[:200])  # (not repr: it could carry the bot URL)
