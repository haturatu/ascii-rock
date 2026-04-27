import os
import tempfile


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
