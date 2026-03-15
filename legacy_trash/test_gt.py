from googletrans import Translator
try:
    translator = Translator()
    print("Translator initialized")
except Exception as e:
    print(f"Translator error: {e}")
