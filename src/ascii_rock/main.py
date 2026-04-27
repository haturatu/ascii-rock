import cv2
import argparse
import concurrent.futures
import collections
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.video.io.VideoFileClip import VideoFileClip

from ascii_rock.portaudio_player import PortAudioError, PortAudioWavPlayer

# ASCII characters from background/empty to foreground/dense.
ASCII_CHARS_DARK_BACKGROUND = " .:-=+*#%@"
ASCII_CHARS_EXPORT_DARK_BACKGROUND = ".:-=+*#%@"
ASCII_CHARS_LIGHT_BACKGROUND = "@%#*+=-:. "
ASCII_TRANSLATION_TABLE_DARK_BACKGROUND = bytes(
    ord(ASCII_CHARS_DARK_BACKGROUND[value * (len(ASCII_CHARS_DARK_BACKGROUND) - 1) // 255])
    for value in range(256)
)
ASCII_TRANSLATION_TABLE_EXPORT_DARK_BACKGROUND = bytes(
    ord(ASCII_CHARS_EXPORT_DARK_BACKGROUND[value * (len(ASCII_CHARS_EXPORT_DARK_BACKGROUND) - 1) // 255])
    for value in range(256)
)
ASCII_TRANSLATION_TABLE_LIGHT_BACKGROUND = bytes(
    ord(ASCII_CHARS_LIGHT_BACKGROUND[value * (len(ASCII_CHARS_LIGHT_BACKGROUND) - 1) // 255])
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
BACKGROUND_THRESHOLD = 24
LIGHT_BACKGROUND_THRESHOLD = 128
EXPORT_FONT_SIZE = 14
EXPORT_FONT_SPACING = 2
EXPORT_PROGRESS_INTERVAL = 120
EXPORT_DEFAULT_WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))
EXPORT_PENDING_MULTIPLIER = 3

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

def frame_to_ascii(frame, width, remove_background=False):
    """Convert a single video frame (numpy array) to an ASCII string."""
    try:
        source_height, source_width = frame.shape[:2]
        height = ascii_height_for(source_width, source_height, width)
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return gray_frame_to_ascii(gray, remove_background=remove_background)
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

def gray_values_to_ascii(frame_bytes, width, height, translation_table):
    """Convert grayscale byte values to an ASCII string."""
    ascii_chars = frame_bytes.translate(translation_table).decode("ascii")
    lines = []
    for i in range(0, width * height, width):
        lines.append(ascii_chars[i:i + width])
    return "\n".join(lines) + "\n"

def estimate_background_value(gray):
    """Estimate background brightness from frame borders."""
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    return float(np.median(border))

def suppress_background_for_ascii(gray):
    """Make near-background pixels render as spaces."""
    background = estimate_background_value(gray)
    light_background = background >= LIGHT_BACKGROUND_THRESHOLD
    foreground = gray.copy()
    background_mask = np.abs(foreground.astype(np.int16) - int(background)) <= BACKGROUND_THRESHOLD
    if light_background:
        foreground[background_mask] = 255
        translation_table = ASCII_TRANSLATION_TABLE_LIGHT_BACKGROUND
    else:
        foreground[background_mask] = 0
        translation_table = ASCII_TRANSLATION_TABLE_DARK_BACKGROUND
    return foreground, translation_table

def gray_frame_to_ascii(gray, remove_background=False, visible_space=False):
    if remove_background:
        foreground, translation_table = suppress_background_for_ascii(gray)
    else:
        foreground = gray
        if visible_space:
            translation_table = ASCII_TRANSLATION_TABLE_EXPORT_DARK_BACKGROUND
        else:
            translation_table = ASCII_TRANSLATION_TABLE_DARK_BACKGROUND
    height, width = foreground.shape
    return gray_values_to_ascii(foreground.tobytes(), width, height, translation_table)

class FFmpegAsciiFrameReader:
    """Reads already-scaled grayscale frames from ffmpeg stdout."""

    def __init__(self, video_path, width, remove_background=False):
        source_width, source_height, fps = probe_video_metadata(video_path)
        self.width = width
        self.height = ascii_height_for(source_width, source_height, width)
        self.remove_background = remove_background
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
                gray = np.frombuffer(frame, dtype=np.uint8).reshape((self.height, self.width))
                self._put_frame(True, gray_frame_to_ascii(gray, remove_background=self.remove_background))
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

class FFmpegGrayFrameReader:
    """Reads scaled grayscale frames from ffmpeg for export workers."""

    def __init__(self, video_path, width):
        source_width, source_height, fps = probe_video_metadata(video_path)
        self.width = width
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = fps
        self.frame_size = self.width * self.height
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

    def read_gray(self):
        frame = self.process.stdout.read(self.frame_size)
        if len(frame) != self.frame_size:
            return False, None
        return True, bytes(frame)

    def release(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

class OpenCvAsciiFrameReader:
    """Fallback reader that decodes full frames with OpenCV."""

    def __init__(self, video_path, width, remove_background=False):
        self.width = width
        self.remove_background = remove_background
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file at {video_path}")
        source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_ascii(self):
        ret, frame = self.cap.read()
        if not ret:
            return False, ""
        return True, frame_to_ascii(frame, self.width, remove_background=self.remove_background)

    def get_pos_msec(self):
        return self.cap.get(cv2.CAP_PROP_POS_MSEC)

    def skip_frame(self):
        ret, _frame = self.cap.read()
        return ret

    def release(self):
        if self.cap.isOpened():
            self.cap.release()

class OpenCvGrayFrameReader:
    """Reads scaled grayscale frames with OpenCV for export workers."""

    def __init__(self, video_path, width):
        self.width = width
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file at {video_path}")
        source_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        source_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.height = ascii_height_for(source_width, source_height, width)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_gray(self):
        ret, frame = self.cap.read()
        if not ret:
            return False, None
        resized = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        return True, gray.tobytes()

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

def load_monospace_font(size):
    candidates = [
        "/usr/share/fonts/TTF/RobotoMono-Regular.ttf",
        "/usr/share/fonts/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/Adwaita/AdwaitaMono-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/droid/DroidSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/Library/Fonts/Menlo.ttc",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()

class AsciiFrameImageRenderer:
    """Render ASCII text frames into fixed-size RGB image frames."""

    def __init__(self, ascii_width, ascii_height, font_size=EXPORT_FONT_SIZE):
        self.ascii_width = ascii_width
        self.ascii_height = ascii_height
        self.font = load_monospace_font(font_size)
        bbox = self.font.getbbox("M")
        ascent, descent = self.font.getmetrics() if hasattr(self.font, "getmetrics") else (font_size, font_size // 4)
        self.char_width = max(1, int(round(self.font.getlength("M"))) if hasattr(self.font, "getlength") else bbox[2] - bbox[0])
        self.line_height = max(1, ascent + descent + EXPORT_FONT_SPACING)
        self.text_y_offset = -bbox[1]
        self.width = self._even(self.char_width * ascii_width)
        self.height = self._even(self.line_height * ascii_height)
        self.glyph_chars, self.glyph_index, self.glyph_stack = self._build_glyph_cache()
        self.dark_export_lookup = np.array(
            [
                ASCII_CHARS_EXPORT_DARK_BACKGROUND[value * (len(ASCII_CHARS_EXPORT_DARK_BACKGROUND) - 1) // 255]
                for value in range(256)
            ],
            dtype="<U1",
        )
        self.dark_lookup = np.array(
            [
                ASCII_CHARS_DARK_BACKGROUND[value * (len(ASCII_CHARS_DARK_BACKGROUND) - 1) // 255]
                for value in range(256)
            ],
            dtype="<U1",
        )
        self.light_lookup = np.array(
            [
                ASCII_CHARS_LIGHT_BACKGROUND[value * (len(ASCII_CHARS_LIGHT_BACKGROUND) - 1) // 255]
                for value in range(256)
            ],
            dtype="<U1",
        )

    def _even(self, value):
        return value if value % 2 == 0 else value + 1

    def _build_glyph_cache(self):
        glyphs = []
        glyph_chars = sorted(set(ASCII_CHARS_DARK_BACKGROUND + ASCII_CHARS_EXPORT_DARK_BACKGROUND + ASCII_CHARS_LIGHT_BACKGROUND))
        for char in glyph_chars:
            tile = Image.new("L", (self.char_width, self.line_height), 0)
            draw = ImageDraw.Draw(tile)
            draw.text((0, self.text_y_offset), char, font=self.font, fill=255)
            glyphs.append(np.asarray(tile, dtype=np.uint8))
        glyph_index = {char: index for index, char in enumerate(glyph_chars)}
        return glyph_chars, glyph_index, np.stack(glyphs, axis=0)

    def render(self, ascii_frame):
        rows = ascii_frame.splitlines()[: self.ascii_height]
        chars = np.full((self.ascii_height, self.ascii_width), " ", dtype="<U1")
        for row_index, line in enumerate(rows):
            line = line[: self.ascii_width].ljust(self.ascii_width)
            chars[row_index] = np.array(list(line), dtype="<U1")
        return self._render_chars(chars)

    def render_gray(self, gray_frame, remove_background=False, visible_space=False):
        gray = np.frombuffer(gray_frame, dtype=np.uint8).reshape((self.ascii_height, self.ascii_width))
        chars = self._gray_to_chars(gray, remove_background=remove_background, visible_space=visible_space)
        return self._render_chars(chars)

    def _gray_to_chars(self, gray, remove_background=False, visible_space=False):
        if remove_background:
            foreground, translation_table = suppress_background_for_ascii(gray)
            lookup = self.light_lookup if translation_table is ASCII_TRANSLATION_TABLE_LIGHT_BACKGROUND else self.dark_lookup
        else:
            foreground = gray
            lookup = self.dark_export_lookup if visible_space else self.dark_lookup
        return lookup[foreground]

    def _render_chars(self, chars):
        glyph_indices = np.zeros(chars.shape, dtype=np.int16)
        for char, index in self.glyph_index.items():
            glyph_indices[chars == char] = index
        text_image = self.glyph_stack[glyph_indices].transpose(0, 2, 1, 3).reshape(
            self.ascii_height * self.line_height,
            self.ascii_width * self.char_width,
        )
        image = np.zeros((self.height, self.width), dtype=np.uint8)
        image[: text_image.shape[0], : text_image.shape[1]] = text_image
        return np.repeat(image[:, :, None], 3, axis=2).tobytes()

def export_ascii_mp4(video_path, output_path, width, no_downconvert, remove_background, workers=EXPORT_DEFAULT_WORKERS):
    """Export ASCII-rendered video to an MP4 file."""
    if no_downconvert:
        reader = OpenCvGrayFrameReader(video_path, width)
    else:
        reader = FFmpegGrayFrameReader(video_path, width)

    renderer = AsciiFrameImageRenderer(width, reader.height)
    fps = reader.fps if reader.fps > 0 else 30.0
    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{renderer.width}x{renderer.height}",
        "-r",
        f"{fps:.6f}",
        "-i",
        "-",
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]

    workers = max(1, workers)
    max_pending = max(1, workers * EXPORT_PENDING_MULTIPLIER)
    process = subprocess.Popen(ffmpeg_command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    frame_count = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            pending = collections.deque()
            reader_done = False

            while pending or not reader_done:
                while not reader_done and len(pending) < max_pending:
                    ret, gray_frame = reader.read_gray()
                    if not ret:
                        reader_done = True
                        break
                    pending.append(executor.submit(renderer.render_gray, gray_frame, remove_background, True))

                if not pending:
                    continue

                process.stdin.write(pending.popleft().result())
                frame_count += 1
                if frame_count % EXPORT_PROGRESS_INTERVAL == 0:
                    print(f"Exported {frame_count} frames...", flush=True)
    finally:
        reader.release()
        if process.stdin:
            process.stdin.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg MP4 export failed: {stderr.strip()}")
    print(f"Exported {frame_count} ASCII frames to {output_path}")

def play_video(video_path, width, play_audio, no_downconvert, remove_background):
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
            reader = OpenCvAsciiFrameReader(video_path, width, remove_background=remove_background)
        else:
            try:
                reader = FFmpegAsciiFrameReader(video_path, width, remove_background=remove_background)
                reader.prebuffer()
            except Exception as e:
                print(f"Could not start ffmpeg frame scaling. Falling back to OpenCV decoding: {e}")
                reader = OpenCvAsciiFrameReader(video_path, width, remove_background=remove_background)

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
    parser.add_argument("--remove-background", action="store_true", help="Treat border-colored background pixels as empty space.")
    parser.add_argument("-o", "--output-mp4", help="Export the ASCII-rendered video to an MP4 file instead of playing it.")
    parser.add_argument("--export-workers", type=int, default=EXPORT_DEFAULT_WORKERS, help=f"Number of parallel frame render workers for --output-mp4. Defaults to {EXPORT_DEFAULT_WORKERS}.")
    
    args = parser.parse_args()

    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found at '{args.video_path}'")
        return

    terminal_width, _ = get_terminal_size()
    output_width = args.width if args.width else terminal_width

    if args.output_mp4:
        export_ascii_mp4(
            args.video_path,
            args.output_mp4,
            output_width,
            args.no_downconvert,
            args.remove_background,
            workers=args.export_workers,
        )
        return

    play_video(args.video_path, output_width, args.music, args.no_downconvert, args.remove_background)

if __name__ == "__main__":
    main()
