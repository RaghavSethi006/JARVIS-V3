"""
Run once to register your face:
    python scripts/register_face.py
"""

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUTPUT_PATH = os.path.join(ROOT, "models", "biometrics", "face_encodings.npy")
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
SAMPLES = 50

try:
    import face_recognition

    USE_FR = True
except ImportError:
    USE_FR = False


def register_with_face_recognition():
    cap = cv2.VideoCapture(0)
    encodings = []
    print("Look at the camera. Capturing 50 samples...")
    while len(encodings) < SAMPLES:
        ret, frame = cap.read()
        if not ret:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb)
        encs = face_recognition.face_encodings(rgb, locs)
        if encs:
            encodings.append(encs[0])
            print(f"  {len(encodings)}/{SAMPLES}", end="\r")
        cv2.imshow("Registration - press Q to abort", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if encodings:
        np.save(OUTPUT_PATH, np.array(encodings))
        print(f"\nSaved {len(encodings)} encodings -> {OUTPUT_PATH}")
    else:
        print("No face detected. Try again with better lighting.")


def register_with_haar():
    """Fallback: capture LBPH samples and train trainer.yml."""
    trainer_dir = os.path.join(ROOT, "models", "biometrics")
    samples_dir = os.path.join(ROOT, "samples")
    os.makedirs(trainer_dir, exist_ok=True)
    os.makedirs(samples_dir, exist_ok=True)

    detector = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    cap = cv2.VideoCapture(0)
    count = 0
    print("Look at the camera. Capturing 50 samples for LBPH training...")
    while count < SAMPLES:
        ret, frame = cap.read()
        if not ret:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.3, 5)
        for (x, y, w, h) in faces:
            count += 1
            cv2.imwrite(f"{samples_dir}/User.{count}.jpg", gray[y : y + h, x : x + w])
            print(f"  {count}/{SAMPLES}", end="\r")
        cv2.imshow("Registration", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    from PIL import Image

    paths = [os.path.join(samples_dir, f) for f in os.listdir(samples_dir)]
    faces = []
    ids = []
    for path in paths:
        img = Image.open(path).convert("L")
        arr = np.array(img, "uint8")
        try:
            face_id = int(os.path.split(path)[-1].split(".")[1])
        except (IndexError, ValueError):
            continue
        detected = detector.detectMultiScale(arr)
        for (x, y, w, h) in detected:
            faces.append(arr[y : y + h, x : x + w])
            ids.append(face_id)
    if faces:
        recognizer.train(faces, np.array(ids))
        trainer_path = os.path.join(trainer_dir, "face_model.yml")
        recognizer.write(trainer_path)
        print(f"\nLBPH model saved -> {trainer_path}")
    else:
        print("No valid Haar face samples found to train LBPH model.")


if __name__ == "__main__":
    if USE_FR:
        register_with_face_recognition()
    else:
        register_with_haar()
