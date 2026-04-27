import cv2
import argparse
import os
import time
from PIL import Image
from moviepy.video.io.VideoFileClip import VideoFileClip

from ascii_rock.portaudio_player import PortAudioError, PortAudioWavPlayer

# ASCII characters from dark to light
ASCII_CHARS = " .:-=+*#%@"
TEMP_AUDIO_FILE = "temp_audio.wav"

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

def play_video(video_path, width, play_audio, no_downconvert):
    """Plays a video file as ASCII art in the terminal."""
    # Imports needed for this function
    import sys
    import select
    import tty
    import termios
    import subprocess
    import tempfile

    temp_video_path = None
    final_video_path = video_path
    audio_extracted = False
    audio_player = None
    old_settings = termios.tcgetattr(sys.stdin) # Get terminal settings at the start

    try:
        # --- FFMPEG Down-conversion Logic (if needed) ---
        if not no_downconvert:
            try:
                probe_cap = cv2.VideoCapture(video_path)
                if not probe_cap.isOpened():
                    print(f"Error: Could not open video file at {video_path}")
                    return
                original_height = int(probe_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                probe_cap.release()

                if original_height > 360:
                    print(f"Video height ({original_height}p) is high. Down-converting to 360p...")
                    
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_f:
                        temp_video_path = temp_f.name
                    
                    ffmpeg_command = f"ffmpeg -i \"{video_path}\" -vf scale=-2:360 \"{temp_video_path}\" -y -hide_banner -loglevel error"
                    
                    result = subprocess.run(ffmpeg_command, shell=True, capture_output=True, text=True)

                    if result.returncode == 0:
                        print("Down-conversion complete.")
                        final_video_path = temp_video_path
                    else:
                        print(f"\n--- FFMPEG Error ---\nDown-conversion failed. Playing original video.\n(Ensure ffmpeg is installed and in your PATH.)\nDetails: {result.stderr}\n--------------------\n")
                        if os.path.exists(temp_video_path):
                            os.remove(temp_video_path)
                        temp_video_path = None
            except Exception as e:
                print(f"An error occurred during video pre-processing: {e}")
        # --- End of FFMPEG Logic ---

        tty.setcbreak(sys.stdin.fileno()) # Set terminal for interactive input

        if play_audio:
            try:
                video_clip = VideoFileClip(final_video_path)
                if video_clip.audio:
                    video_clip.audio.write_audiofile(
                        TEMP_AUDIO_FILE,
                        codec="pcm_s16le",
                        ffmpeg_params=["-ac", "2"],
                        logger=None,
                    )
                    audio_extracted = True
                    video_clip.close()
                    audio_player = PortAudioWavPlayer(TEMP_AUDIO_FILE)
                    audio_player.play()
                else:
                    video_clip.close()
                    print("No audio track found in the video.")
                    play_audio = False
            except PortAudioError as e:
                print(f"Could not play audio with PortAudio: {e}")
                play_audio = False
            except Exception as e:
                print(f"Could not process audio: {e}")
                play_audio = False

        cap = cv2.VideoCapture(final_video_path)
        if not cap.isOpened():
            print(f"Error: Could not open video file at {final_video_path}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        delay = 1 / fps if fps > 0 else 1/30

        running = True
        paused = False
        while running:
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                key = sys.stdin.read(1)
                if key == ' ':
                    paused = not paused
                    if paused:
                        if play_audio and audio_extracted and audio_player: audio_player.pause()
                    else:
                        if play_audio and audio_extracted and audio_player: audio_player.unpause()
                elif key.lower() == 'q':
                    running = False

            if not running: break
            if paused:
                time.sleep(0.1)
                continue

            ret, frame = cap.read()
            if not ret:
                running = False
                continue
            
            if play_audio and audio_extracted and audio_player and not audio_player.is_busy():
                running = False
                continue

            ascii_frame = frame_to_ascii(frame, width)
            os.system('cls' if os.name == 'nt' else 'clear')
            print(ascii_frame, end='', flush=True)

            if play_audio and audio_extracted and audio_player and audio_player.is_busy():
                video_ts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                audio_ts_ms = audio_player.get_pos()
                if audio_ts_ms > 0 and video_ts_ms > audio_ts_ms:
                    sync_delay = (video_ts_ms - audio_ts_ms) / 1000.0
                    if sync_delay > 0.001: time.sleep(sync_delay)
            else:
                time.sleep(delay)

    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred during playback: {e}")
    finally:
        # This block ensures cleanup happens even if errors occur
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        if 'cap' in locals() and cap.isOpened():
            cap.release()
        
        if 'audio_player' in locals() and audio_player:
            audio_player.stop()

        # Cleanup temporary files
        if audio_extracted and os.path.exists(TEMP_AUDIO_FILE):
            os.remove(TEMP_AUDIO_FILE)
        if temp_video_path and os.path.exists(temp_video_path):
            os.remove(temp_video_path)

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter, 
        description="Play video files as ASCII art in the terminal.\n\nControls:\n  Space: Pause/Resume\n  q: Quit"
    )
    parser.add_argument("video_path", help="Path to the video file.")
    parser.add_argument("-w", "--width", type=int, help="Width of the ASCII output in characters. Defaults to terminal width.")
    parser.add_argument("-m", "--music", action="store_true", help="Play audio from the video file.")
    parser.add_argument("--no-downconvert", action="store_true", help="Disable automatic 360p down-conversion for high-res videos.")
    
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    play_video(args.video_path, output_width, args.music, args.no_downconvert)

if __name__ == "__main__":
    main()
