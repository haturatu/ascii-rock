import cv2
import numpy as np

from ascii_rock.constants import (
    ASCII_TRANSLATION_TABLE_DARK_BACKGROUND,
    ASCII_TRANSLATION_TABLE_EXPORT_DARK_BACKGROUND,
    ASCII_TRANSLATION_TABLE_LIGHT_BACKGROUND,
    BACKGROUND_THRESHOLD,
    LIGHT_BACKGROUND_THRESHOLD,
)


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
