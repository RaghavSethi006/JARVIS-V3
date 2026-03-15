import cv2
import os
import numpy as np
import asyncio
from core.event_bus import EventBus
from core.logger import logger

try:
    import face_recognition
    HAS_FR = True
except ImportError:
    HAS_FR = False
    logger.warning("face_recognition library not found. Falling back to Haar Cascades.")

ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MODELS_DIR = os.path.join(ROOT_DIR, "models", "biometrics")
FACE_ENCODINGS_PATH = os.path.join(MODELS_DIR, "face_encodings.npy")
TRAINER_PATH = os.path.join(MODELS_DIR, "face_model.yml")
# Backward-compatible alias used by existing tests/tooling.
FACE_DATA_PATH = TRAINER_PATH
HAAR_XML = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

class BiometricService:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.known_face_encodings = []   # For face_recognition
        self.known_face_names = []
        
        # Load known faces on init
        self.load_faces()
        
        self.bus.subscribe("auth_login", self.handle_login)
        self.bus.subscribe("auth_register", self.handle_register)

    def load_faces(self):
        if HAS_FR and os.path.exists(FACE_ENCODINGS_PATH):
            try:
                self.known_face_encodings = np.load(FACE_ENCODINGS_PATH, allow_pickle=True).tolist()
                self.known_face_names = ["User"] * len(self.known_face_encodings)
            except Exception as e:
                logger.error(f"Failed to load faces: {e}")

    async def handle_login(self, event):
        logger.info("Starting Biometric Auth...")
        if HAS_FR and self.known_face_encodings:
            success = await self._auth_with_fr()
        else:
            success = await self.auth_with_haar()
            
        if success:
            logger.info("Authentication Successful")
            await self.bus.emit("auth_success", {"user": "User"})
            await self.bus.emit("tts_speak", "Welcome back, sir.")
        else:
            logger.info("Authentication Failed")
            await self.bus.emit("auth_failed")
            await self.bus.emit("tts_speak", "Face not recognized.")

    async def handle_register(self, event):
        logger.info("Starting Face Registration...")
        if HAS_FR:
            await self._register_with_fr()
        else:
            await self.register_with_haar()

    # --- Methods ---
    async def _auth_with_fr(self):
        # Run in executor
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.__capture_and_verify_fr)

    def __capture_and_verify_fr(self):
        cap = cv2.VideoCapture(0)
        found = False
        for _ in range(50): # Try for ~50 frames
            ret, frame = cap.read()
            if not ret: continue
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame)
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

            for face_encoding in face_encodings:
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding)
                if True in matches:
                    found = True
                    break
            if found: break
        
        cap.release()
        return found
        
    async def _register_with_fr(self):
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, self.__capture_and_save_fr)
        if success:
            await self.bus.emit("tts_speak", "Face registered successfully.")

    def __capture_and_save_fr(self):
        cap = cv2.VideoCapture(0)
        logger.info("Look at the camera...")
        saved = False
        
        for _ in range(100):
            ret, frame = cap.read()
            if not ret: continue
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame)
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
            
            if len(face_encodings) > 0:
                self.known_face_encodings.append(face_encodings[0])
                np.save(FACE_ENCODINGS_PATH, self.known_face_encodings)
                saved = True
                break
        
        cap.release()
        return saved

    async def auth_with_haar(self):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._verify_lbph)

    def _verify_lbph(self) -> bool:
        if not os.path.exists(TRAINER_PATH):
            logger.warning("LBPH face_model.yml not found. Run scripts/register_face.py first.")
            return False

        try:
            recognizer = cv2.face.LBPHFaceRecognizer_create()
        except Exception as exc:
            logger.warning("OpenCV LBPH recognizer unavailable: %s", exc)
            return False
        recognizer.read(TRAINER_PATH)
        detector = cv2.CascadeClassifier(HAAR_XML)

        cap = cv2.VideoCapture(0)
        result = False
        confidence_threshold = 60
        for _ in range(60):
            ret, frame = cap.read()
            if not ret:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, 1.3, 5)
            for (x, y, w, h) in faces:
                face_roi = gray[y:y + h, x:x + w]
                if face_roi.size == 0:
                    continue
                try:
                    _, confidence = recognizer.predict(face_roi)
                except Exception:
                    continue
                logger.info(
                    "LBPH confidence: %.1f (threshold: %s)",
                    confidence,
                    confidence_threshold,
                )
                if confidence < confidence_threshold:
                    result = True
                    break
            if result:
                break
        cap.release()
        return result

    async def register_with_haar(self):
        loop = asyncio.get_event_loop()
        await self.bus.emit("tts_speak", "Look at the camera. Registering face...")
        success = await loop.run_in_executor(None, self._haar_register)
        if success:
            await self.bus.emit("tts_speak", "Face registered successfully.")
        else:
            await self.bus.emit("tts_speak", "Registration failed. Please try again.")

    def _haar_register(self) -> bool:
        detector = cv2.CascadeClassifier(HAAR_XML)
        try:
            recognizer = cv2.face.LBPHFaceRecognizer_create()
        except Exception as exc:
            logger.warning("OpenCV LBPH recognizer unavailable: %s", exc)
            return False

        cap = cv2.VideoCapture(0)
        faces_data = []
        labels = []
        collected = 0

        for _ in range(200):
            ret, frame = cap.read()
            if not ret:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = detector.detectMultiScale(gray, 1.3, 5)
            for (x, y, w, h) in detected:
                face_roi = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
                faces_data.append(face_roi)
                labels.append(1)
                collected += 1
            if collected >= 50:
                break

        cap.release()
        if collected < 10:
            return False

        recognizer.train(faces_data, np.array(labels))
        trainer_dir = os.path.dirname(TRAINER_PATH)
        os.makedirs(trainer_dir, exist_ok=True)
        recognizer.write(TRAINER_PATH)
        logger.info(f"LBPH model saved to {TRAINER_PATH} ({collected} samples)")
        return True
