import os
import select
import sys
import termios
import time
import tty

from moviepy.video.io.VideoFileClip import VideoFileClip

from ascii_rock.portaudio_player import PortAudioError, PortAudioWavPlayer
from ascii_rock.renderers import TerminalRenderer
from ascii_rock.tempfiles import create_temp_audio_path
from ascii_rock.video import FFmpegAsciiFrameReader, OpenCvAsciiFrameReader, skip_video_frames_to_sync


def play_video(video_path, width, play_audio, no_downconvert, remove_background):
    """Plays a video file as ASCII art in the terminal."""
    audio_extracted = False
    audio_path = None
    audio_player = None
    reader = None
    renderer = TerminalRenderer(sys.stdout)
    old_settings = termios.tcgetattr(sys.stdin)

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

        tty.setcbreak(sys.stdin.fileno())
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
        delay = 1 / fps if fps > 0 else 1 / 30

        running = True
        paused = False
        while running:
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                key = sys.stdin.read(1)
                if key == " ":
                    paused = not paused
                    if paused:
                        if play_audio and audio_extracted and audio_player:
                            audio_player.pause()
                    else:
                        if play_audio and audio_extracted and audio_player:
                            audio_player.unpause()
                elif key.lower() == "q":
                    running = False

            if not running:
                break
            if paused:
                time.sleep(0.1)
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
                    if sync_delay > 0.001:
                        time.sleep(sync_delay)
            else:
                time.sleep(delay)

    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred during playback: {e}")
    finally:
        renderer.stop()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        if reader:
            reader.release()

        if audio_player:
            audio_player.stop()

        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
