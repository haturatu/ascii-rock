import cv2
import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from PIL import Image
from moviepy.video.io.VideoFileClip import VideoFileClip

from ascii_rock.portaudio_player import PortAudioError, PortAudioWavPlayer

# ASCII characters from dark to light
ASCII_CHARS = " .:-=+*#%@"
ASCII_TRANSLATION_TABLE = bytes(
    ord(ASCII_CHARS[value * (len(ASCII_CHARS) - 1) // 255])
    for value in range(256)
)
FRAME_PREFETCH_SECONDS = 10.0
INITIAL_PREBUFFER_SECONDS = 3.0
INITIAL_PREBUFFER_TIMEOUT_SECONDS = 10.0
SYNC_TOLERANCE_MS = 80.0
MAX_SYNC_SKIP_FRAMES = 30
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"
ANSI_HOME = "\x1b[H"
ANSI_CLEAR_SCREEN = "\x1b[2J"
ANSI_CLEAR_TO_END = "\x1b[J"

def get_tmpfs_dir():
    """Return a tmpfs directory when one is available."""
    tmpfs_dir = os.environ.get("ASCII_ROCK_TMPFS_DIR")
    if tmpfs_dir and os.path.isdir(tmpfs_dir):
        return tmpfs_dir
    if os.name == "posix" and os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK):
        return "/dev/shm"
    return tempfile.gettempdir()

def create_temp_audio_path():
    fd, path = tempfile.mkstemp(prefix="ascii-rock-audio-", suffix=".wav", dir=get_tmpfs_dir())
    os.close(fd)
    return path

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

def _parse_fps(value):
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator = float(denominator)
        if denominator == 0:
            return 0.0
        return float(numerator) / denominator
    return float(value)

def probe_video_metadata(video_path):
    """Reads basic video metadata with ffprobe."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("No video stream found")

    stream = streams[0]
    fps = _parse_fps(stream.get("avg_frame_rate")) or _parse_fps(stream.get("r_frame_rate")) or 30.0
    return int(stream["width"]), int(stream["height"]), fps

def ascii_height_for(source_width, source_height, ascii_width):
    aspect_ratio = source_height / float(source_width)
    return max(1, int(aspect_ratio * ascii_width * 0.55))

def gray_bytes_to_ascii(frame_bytes, width, height):
    """Convert one grayscale rawvideo frame to an ASCII string."""
    ascii_chars = frame_bytes.translate(ASCII_TRANSLATION_TABLE).decode("ascii")
    lines = []
    for i in range(0, width * height, width):
        lines.append(ascii_chars[i:i + width])
    return "\n".join(lines) + "\n"

class FFmpegAsciiFrameReader:
    """Reads already-scaled grayscale frames from ffmpeg stdout."""

    def __init__(self, video_path, width):
        source_width, source_height, fps = probe_video_metadata(video_path)
        self.width = width
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = fps
        self.frame_size = self.width * self.height
        self.frame_index = 0
        self._stopped = threading.Event()
        self._frames = queue.Queue(maxsize=max(8, int(self.fps * FRAME_PREFETCH_SECONDS)))
        self._reader_error = None
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-an",
                "-vf",
                f"scale={self.width}:{self.height}:flags=fast_bilinear,format=gray",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._prefetch_frames, name="ascii-rock-ffmpeg-reader", daemon=True)
        self._thread.start()

    def read_ascii(self):
        ret, ascii_frame = self._frames.get()
        if self._reader_error:
            raise self._reader_error
        if not ret:
            return False, ""
        self.frame_index += 1
        return True, ascii_frame

    def get_pos_msec(self):
        return self.frame_index * 1000.0 / self.fps if self.fps > 0 else 0.0

    def skip_frame(self):
        ret, _ascii_frame = self.read_ascii()
        return ret

    def prebuffer(self, seconds=INITIAL_PREBUFFER_SECONDS, timeout=INITIAL_PREBUFFER_TIMEOUT_SECONDS):
        target = min(self._frames.maxsize, max(1, int(self.fps * seconds)))
        deadline = time.monotonic() + timeout
        while self._frames.qsize() < target and time.monotonic() < deadline:
            if self._reader_error:
                raise self._reader_error
            if self.process.poll() is not None:
                break
            time.sleep(0.02)

    def release(self):
        self._stopped.set()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _prefetch_frames(self):
        try:
            while not self._stopped.is_set():
                frame = self.process.stdout.read(self.frame_size)
                if len(frame) != self.frame_size:
                    self._put_frame(False, "")
                    break
                self._put_frame(True, gray_bytes_to_ascii(frame, self.width, self.height))
        except Exception as e:
            self._reader_error = e
            self._put_frame(False, "")

    def _put_frame(self, ret, ascii_frame):
        while not self._stopped.is_set():
            try:
                self._frames.put((ret, ascii_frame), timeout=0.1)
                return
            except queue.Full:
                continue

class OpenCvAsciiFrameReader:
    """Fallback reader that decodes full frames with OpenCV."""

    def __init__(self, video_path, width):
        self.width = width
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file at {video_path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_ascii(self):
        ret, frame = self.cap.read()
        if not ret:
            return False, ""
        return True, frame_to_ascii(frame, self.width)

    def get_pos_msec(self):
        return self.cap.get(cv2.CAP_PROP_POS_MSEC)

    def skip_frame(self):
        ret, _frame = self.cap.read()
        return ret

    def release(self):
        if self.cap.isOpened():
            self.cap.release()

def skip_video_frames_to_sync(reader, audio_ts_ms):
    skipped = 0
    while skipped < MAX_SYNC_SKIP_FRAMES:
        video_ts_ms = reader.get_pos_msec()
        if audio_ts_ms - video_ts_ms <= SYNC_TOLERANCE_MS:
            break
        if not reader.skip_frame():
            return False, skipped
        skipped += 1
    return True, skipped

class TerminalRenderer:
    """Draw ASCII frames without clearing between erase and paint."""

    def __init__(self, output):
        self.output = output
        self.last_line_count = 0

    def start(self):
        self.output.write(ANSI_HIDE_CURSOR + ANSI_CLEAR_SCREEN + ANSI_HOME)
        self.output.flush()

    def draw(self, ascii_frame):
        line_count = ascii_frame.count("\n")
        if line_count < self.last_line_count:
            suffix = ANSI_CLEAR_TO_END
        else:
            suffix = ""
        self.output.write(ANSI_HOME + ascii_frame + suffix)
        self.output.flush()
        self.last_line_count = line_count

    def stop(self):
        self.output.write(ANSI_SHOW_CURSOR)
        self.output.flush()

def play_video(video_path, width, play_audio, no_downconvert):
    """Plays a video file as ASCII art in the terminal."""
    # Imports needed for this function
    import sys
    import select
    import tty
    import termios

    audio_extracted = False
    audio_path = None
    audio_player = None
    reader = None
    renderer = TerminalRenderer(sys.stdout)
    old_settings = termios.tcgetattr(sys.stdin) # Get terminal settings at the start

    try:
        if no_downconvert:
            reader = OpenCvAsciiFrameReader(video_path, width)
        else:
            try:
                reader = FFmpegAsciiFrameReader(video_path, width)
                reader.prebuffer()
            except Exception as e:
                print(f"Could not start ffmpeg frame scaling. Falling back to OpenCV decoding: {e}")
                reader = OpenCvAsciiFrameReader(video_path, width)

        tty.setcbreak(sys.stdin.fileno()) # Set terminal for interactive input
        renderer.start()

        if play_audio:
            try:
                video_clip = VideoFileClip(video_path)
                if video_clip.audio:
                    audio_path = create_temp_audio_path()
                    video_clip.audio.write_audiofile(
                        audio_path,
                        codec="pcm_s16le",
                        ffmpeg_params=["-ac", "2"],
                        logger=None,
                    )
                    audio_extracted = True
                    video_clip.close()
                    audio_player = PortAudioWavPlayer(audio_path)
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

        fps = reader.fps
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

            if play_audio and audio_extracted and audio_player and not audio_player.is_busy():
                running = False
                continue

            if play_audio and audio_extracted and audio_player and audio_player.is_busy():
                running, _skipped = skip_video_frames_to_sync(reader, audio_player.get_pos())
                if not running:
                    continue

            ret, ascii_frame = reader.read_ascii()
            if not ret:
                running = False
                continue

            renderer.draw(ascii_frame)

            if play_audio and audio_extracted and audio_player and audio_player.is_busy():
                video_ts_ms = reader.get_pos_msec()
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
        renderer.stop()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        if reader:
            reader.release()
        
        if 'audio_player' in locals() and audio_player:
            audio_player.stop()

        # Cleanup temporary files
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter, 
        description="Play video files as ASCII art in the terminal.\n\nControls:\n  Space: Pause/Resume\n  q: Quit"
    )
    parser.add_argument("video_path", help="Path to the video file.")
    parser.add_argument("-w", "--width", type=int, help="Width of the ASCII output in characters. Defaults to terminal width.")
    parser.add_argument("-m", "--music", action="store_true", help="Play audio from the video file.")
    parser.add_argument("--no-downconvert", action="store_true", help="Disable ffmpeg frame scaling and decode full frames with OpenCV.")
    
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    play_video(args.video_path, output_width, args.music, args.no_downconvert)

if __name__ == "__main__":
    main()
