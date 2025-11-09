import cv2
import argparse
import os
import time
from PIL import Image

# ASCII characters from dark to light
ASCII_CHARS = " .:-=+*#%@"

def get_terminal_size():
    """Gets the current size of the terminal."""
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24 # Default size

def resize_image(image, new_width=100):
    """Resize image with aspect ratio preservation."""
    width, height = image.size
    # Adjust for character aspect ratio (characters are taller than they are wide)
    aspect_ratio = height / float(width)
    new_height = int(aspect_ratio * new_width * 0.55)
    resized_image = image.resize((new_width, new_height))
    return resized_image

def grayscale(image):
    """Convert image to grayscale."""
    return image.convert("L")

def pixels_to_ascii(image):
    """Convert pixels to a string of ASCII characters."""
    pixels = image.getdata()
    ascii_str = ""
    for pixel_value in pixels:
        # Map pixel value to ASCII character
        ascii_str += ASCII_CHARS[pixel_value * (len(ASCII_CHARS) - 1) // 255]
    return ascii_str

def frame_to_ascii(frame, width):
    """Convert a single video frame (numpy array) to an ASCII string."""
    try:
        # Convert numpy array from OpenCV to a Pillow Image
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        
        # Resize, convert to grayscale, and then to ASCII
        resized_img = resize_image(img, new_width=width)
        grayscale_img = grayscale(resized_img)
        ascii_str = pixels_to_ascii(grayscale_img)
        
        # Format the ASCII string with newlines
        img_width = resized_img.width
        ascii_str_formatted = ""
        for i in range(0, len(ascii_str), img_width):
            ascii_str_formatted += ascii_str[i:i+img_width] + "\n"
            
        return ascii_str_formatted
    except Exception as e:
        print(f"Error converting frame: {e}")
        return ""

def play_video(video_path, width):
    """Plays a video file as ASCII art in the terminal."""
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Error: Could not open video file at {video_path}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        # If FPS is 0, it might be a webcam or a corrupted file. Use a default.
        delay = 1 / fps if fps > 0 else 1/30 

        while True:
            ret, frame = cap.read()
            if not ret:
                # Loop the video
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            # Get ASCII representation of the frame
            ascii_frame = frame_to_ascii(frame, width)

            # Clear the terminal and print the frame
            os.system('cls' if os.name == 'nt' else 'clear')
            print(ascii_frame, end='', flush=True)

            # Wait for the next frame
            time.sleep(delay)

    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'cap' in locals() and cap.isOpened():
            cap.release()

def main():
    parser = argparse.ArgumentParser(description="Play video files as ASCII art in the terminal.")
    parser.add_argument("video_path", help="Path to the video file.")
    parser.add_argument("-w", "--width", type=int, help="Width of the ASCII output in characters. Defaults to terminal width.")
    
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    play_video(args.video_path, output_width)

if __name__ == "__main__":
    main()
