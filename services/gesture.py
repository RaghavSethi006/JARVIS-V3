import cv2
import mediapipe as mp
import pyautogui
import math
import asyncio
import threading
from enum import IntEnum
from core.event_bus import EventBus
from core.logger import logger

# --- Enums (Ported from Legacy) ---
class Gest(IntEnum):
    FIST = 0
    PINKY = 1
    RING = 2
    MID = 4
    LAST3 = 7
    INDEX = 8
    FIRST2 = 12
    LAST4 = 15
    THUMB = 16
    PALM = 31
    V_GEST = 33
    TWO_FINGER_CLOSED = 34
    PINCH_MAJOR = 35
    PINCH_MINOR = 36

class HLabel(IntEnum):
    MINOR = 0
    MAJOR = 1

# --- Logic Helper (Simplified) ---
class HandHelper:
    def __init__(self):
        self.finger = 0
        self.ori_gesture = Gest.PALM
        self.prev_gesture = Gest.PALM
        
    def get_signed_dist(self, point, hand_landmarks):
        sign = -1
        if hand_landmarks.landmark[point[0]].y < hand_landmarks.landmark[point[1]].y:
            sign = 1
        dist = (hand_landmarks.landmark[point[0]].x - hand_landmarks.landmark[point[1]].x)**2
        dist += (hand_landmarks.landmark[point[0]].y - hand_landmarks.landmark[point[1]].y)**2
        return math.sqrt(dist)*sign

    def get_dist(self, point, hand_landmarks):
        dist = (hand_landmarks.landmark[point[0]].x - hand_landmarks.landmark[point[1]].x)**2
        dist += (hand_landmarks.landmark[point[0]].y - hand_landmarks.landmark[point[1]].y)**2
        return math.sqrt(dist)

    def get_gesture(self, hand_landmarks):
        # Simplified gesture detection logic
        if not hand_landmarks:
            return Gest.PALM
            
        points = [[8,5,0],[12,9,0],[16,13,0],[20,17,0]]
        self.finger = 0
        for point in points:
            dist = self.get_signed_dist(point[:2], hand_landmarks)
            dist2 = self.get_signed_dist(point[1:], hand_landmarks)
            try:
                ratio = round(dist/dist2,1)
            except:
                ratio = round(dist/0.01,1)
            self.finger = self.finger << 1
            if ratio > 0.5:
                self.finger = self.finger | 1
        
        # Check specific gestures
        if self.finger == Gest.FIRST2:
             if self.get_dist([8,12], hand_landmarks) < 0.05:
                 return Gest.TWO_FINGER_CLOSED
             return Gest.V_GEST
        
        if self.finger == Gest.FIST:
            return Gest.FIST
            
        if self.finger == Gest.PALM:
            return Gest.PALM
            
        return self.finger


class GestureService:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self._loop = None
        
        # MediaPipe Setup
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            max_num_hands=1,
            min_detection_confidence=0.7,
            min_tracking_confidence=0.5
        )
        
        self.helper = HandHelper()
        self.screen_w, self.screen_h = pyautogui.size()
        
        # Sub to events
        self.bus.subscribe("toggle_gesture_control", self.toggle)

    def toggle(self, event):
        self._loop = asyncio.get_event_loop()
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.running:
            return
        logger.info("Starting Gesture Service...")
        self.running = True
        self.thread = threading.Thread(target=self._flame_loop)
        self.thread.start()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.bus.emit("tts_speak", "Gesture control activated."),
                self._loop,
            )

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        logger.info("Gesture Service Stopped.")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.bus.emit("tts_speak", "Gesture control deactivated."),
                self._loop,
            )

    def _flame_loop(self):
        cap = cv2.VideoCapture(0)
        
        while self.running and cap.isOpened():
            success, image = cap.read()
            if not success:
                continue

            image = cv2.flip(image, 1)
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = self.hands.process(image_rgb)

            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    gesture = self.helper.get_gesture(hand_landmarks)
                    self._handle_action(gesture, hand_landmarks)
            
            # Optional: Show preview window?
            # cv2.imshow('Gesture Preview', image)
            # if cv2.waitKey(5) & 0xFF == 27:
            #    break
        
        cap.release()
        cv2.destroyAllWindows()

    def _handle_action(self, gesture, landmarks):
        # Index finger tip
        x = int(landmarks.landmark[8].x * self.screen_w)
        y = int(landmarks.landmark[8].y * self.screen_h)

        if gesture == Gest.V_GEST:
            # Move Mouse
            pyautogui.moveTo(x, y, duration=0.1)
        
        elif gesture == Gest.FIST:
            # Drag / Click
             pyautogui.mouseDown()
        
        elif gesture == Gest.PALM:
             pyautogui.mouseUp()
        
        # Debug
        # print(f"Gesture: {gesture}")
