"""Face unlock (JARVIS's face auth, rebuilt): OpenCV's YuNet finds the face, SFace turns it into 128 numbers, and
those are compared with the user's enrolled ones (data/face_profile.npy, from scripts/enroll_face.py). Local, CPU only;
no photos are kept. It keeps other people from using Karen while they're away. It is not strong security: a good
photo of them may pass, and it can't unlock Windows (no Windows Hello camera on this laptop)."""
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
        """Look for them: (True, best score, "match") once `need` frames in a row match.
        Otherwise the reason: "camera" (no picture), "no face", or "not them"."""
        if self.profile is None:
            return False, 0.0, "no profile"
        got = seen = hits = 0
        best = 0.0
        for frame in self.frames(seconds):
            got += 1
            feat, _ = self.embed(frame)
            if feat is None:
                hits = 0
                continue
            seen += 1
            s = self.score(feat)
            best = max(best, s)
            hits = hits + 1 if s >= self.threshold else 0
            if hits >= need:
                return True, best, "match"
        return False, best, "camera" if not got else "no face" if not seen else "not them"
