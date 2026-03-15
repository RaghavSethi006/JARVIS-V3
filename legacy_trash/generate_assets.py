import os
from PIL import Image, ImageDraw, ImageFont

# Define assets to create
assets = {
    "logo.png": (561, 81, "blue", "JARVIS AI"),
    "startButton.png": (141, 61, "green", "START"),
    "exitButton.png": (141, 61, "red", "EXIT"),
    "retryButton.png": (141, 61, "orange", "RETRY"),
    "backButton.png": (141, 61, "gray", "BACK"),
    "loginButton.png": (141, 61, "cyan", "LOGIN"),
    "newUser.png": (171, 61, "purple", "NEW USER"),
    "signUp.png": (171, 61, "purple", "SIGN UP"),
    "blkimg.png": (100, 100, "black", ""),
    "arcreactor.gif": (200, 200, "cyan", "Arc Reactor"),
    "voicerecog.gif": (200, 200, "blue", "Listening..."),
    "listening.gif": (200, 200, "blue", "Mic On"),
    "samplegui3.gif": (800, 600, "black", "Loading..."),
    "loginFailed.gif": (511, 301, "red", "Login Failed"),
    "ironman.webp": (200, 200, "red", "Iron Man") # Existing but ensuring
}

target_dir = os.path.join("JarvisGUI", "JarvisImages")
if not os.path.exists(target_dir):
    os.makedirs(target_dir)

def create_image(filename, width, height, color, text):
    path = os.path.join(target_dir, filename)
    # Create image
    img = Image.new('RGB', (width, height), color=color)
    d = ImageDraw.Draw(img)
    
    # Add text if possible
    try:
        # Default font
        d.text((10, 10), text, fill=(255, 255, 255))
    except Exception as e:
        print(f"Could not add text to {filename}: {e}")
        
    # Save
    img.save(path)
    print(f"Created {path}")

def main():
    print(f"Generating assets in {target_dir}...")
    for name, props in assets.items():
        create_image(name, props[0], props[1], props[2], props[3])
    print("Asset generation complete.")

if __name__ == "__main__":
    main()
