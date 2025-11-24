import cv2
import argparse
import os
import time
from PIL import Image

# Suppress the Pygame welcome message
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pygame.pkgdata")
import pygame
from moviepy.video.io.VideoFileClip import VideoFileClip

# ASCII characters from dark to light
ASCII_CHARS = " .:-=+*#%@"
TEMP_AUDIO_FILE = "temp_audio.mp3"

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

def play_video(video_path, width, play_audio):
    """Plays a video file as ASCII art in the terminal."""
    audio_extracted = False
    try:
        if play_audio:
            try:
                # Extract audio from video
                video_clip = VideoFileClip(video_path)
                if video_clip.audio:
                    video_clip.audio.write_audiofile(TEMP_AUDIO_FILE, logger=None)
                    video_clip.close()
                    audio_extracted = True
                    
                    # Initialize pygame mixer and play audio
                    pygame.mixer.init()
                    pygame.mixer.music.load(TEMP_AUDIO_FILE)
                    pygame.mixer.music.play()
                else:
                    print("No audio track found in the video.")
                    play_audio = False
            except Exception as e:
                print(f"Could not process audio: {e}")
                play_audio = False

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Error: Could not open video file at {video_path}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        delay = 1 / fps if fps > 0 else 1/30 

        while True:
            ret, frame = cap.read()
            if not ret:
                # Loop the video
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                if play_audio and audio_extracted:
                    pygame.mixer.music.rewind()
                continue

            ascii_frame = frame_to_ascii(frame, width)

            os.system('cls' if os.name == 'nt' else 'clear')
            print(ascii_frame, end='', flush=True)

            # --- Synchronization ---
            if play_audio and audio_extracted:
                video_ts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                audio_ts_ms = pygame.mixer.music.get_pos()
                
                # If video is ahead of audio, wait
                if video_ts_ms > audio_ts_ms:
                    delay_s = (video_ts_ms - audio_ts_ms) / 1000.0
                    time.sleep(delay_s)
                # If audio is far ahead, we could skip video frames, but for now, we play them as fast as possible.
            else:
                # Fallback to FPS-based delay if no audio
                time.sleep(delay)

    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'cap' in locals() and cap.isOpened():
            cap.release()
        if play_audio and audio_extracted:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        if audio_extracted and os.path.exists(TEMP_AUDIO_FILE):
            os.remove(TEMP_AUDIO_FILE)

def main():
    parser = argparse.ArgumentParser(description="Play video files as ASCII art in the terminal.")
    parser.add_argument("video_path", help="Path to the video file.")
    parser.add_argument("-w", "--width", type=int, help="Width of the ASCII output in characters. Defaults to terminal width.")
    parser.add_argument("-m", "--music", action="store_true", help="Play audio from the video file.")
    
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    play_video(args.video_path, output_width, args.music)

if __name__ == "__main__":
    main()
