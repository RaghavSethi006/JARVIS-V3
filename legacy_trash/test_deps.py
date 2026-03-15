import sys
import os
import pandas as pd
import spacy
import pyttsx3
from PyQt5.QtWidgets import QApplication, QWidget

print("Imports successful")
try:
    nlp = spacy.load('en_core_web_sm')
    print("Spacy model loaded")
except Exception as e:
    print(f"Spacy error: {e}")

try:
    engine = pyttsx3.init()
    print("Pyttsx3 initialized")
except Exception as e:
    print(f"Pyttsx3 error: {e}")

print("All tests passed")
