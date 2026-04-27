import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ascii_rock.ascii import suppress_background_for_ascii
from ascii_rock.constants import (
    ANSI_ALT_SCREEN,
    ANSI_CLEAR_LINE,
    ANSI_CLEAR_SCREEN,
    ANSI_DISABLE_WRAP,
    ANSI_ENABLE_WRAP,
    ANSI_HIDE_CURSOR,
    ANSI_HOME,
    ANSI_MAIN_SCREEN,
    ANSI_SHOW_CURSOR,
    ASCII_CHARS_DARK_BACKGROUND,
    ASCII_CHARS_EXPORT_DARK_BACKGROUND,
    ASCII_CHARS_LIGHT_BACKGROUND,
    ASCII_TRANSLATION_TABLE_LIGHT_BACKGROUND,
    EXPORT_FONT_SIZE,
    EXPORT_FONT_SPACING,
)


def get_terminal_size():
    """Gets the current size of the terminal."""
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24


class TerminalRenderer:
    """Draw ASCII frames without clearing between erase and paint."""

    def __init__(self, output):
        self.output = output
        self.last_line_count = 0

    def start(self):
        self.output.write(ANSI_ALT_SCREEN + ANSI_HIDE_CURSOR + ANSI_DISABLE_WRAP + ANSI_CLEAR_SCREEN + ANSI_HOME)
        self.output.flush()

    def draw(self, ascii_frame):
        terminal_width, terminal_height = get_terminal_size()
        max_width = max(1, terminal_width - 1)
        max_height = max(1, terminal_height - 1)
        lines = ascii_frame.splitlines()
        visible_lines = lines[:max_height]
        line_count = len(visible_lines)

        chunks = []
        for row, line in enumerate(visible_lines, start=1):
            chunks.append(f"\x1b[{row};1H{line[:max_width]}{ANSI_CLEAR_LINE}")
        for row in range(line_count + 1, self.last_line_count + 1):
            if row > max_height:
                break
            chunks.append(f"\x1b[{row};1H{ANSI_CLEAR_LINE}")
        self.output.write("".join(chunks))
        self.output.flush()
        self.last_line_count = line_count

    def stop(self):
        self.output.write(ANSI_ENABLE_WRAP + ANSI_SHOW_CURSOR + ANSI_MAIN_SCREEN)
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
