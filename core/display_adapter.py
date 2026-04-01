"""Framebuffer output helpers and fallback display handling."""

from dataclasses import dataclass
import hashlib
from typing import Callable

from PIL import Image, ImageDraw


_LAST_FRAME_HASH: bytes | None = None
_RGB565_BUFFER: bytearray = bytearray()


@dataclass(frozen=True, slots=True)
class DisplayAdapterConfig:
    fb_device: str
    width: int
    height: int
    display_x_shift: int


@dataclass(frozen=True, slots=True)
class DisplayAdapterLogger:
    log_exception: Callable[[str, str, Exception], None]
    log_message: Callable[[str, str], None]


def create_error_placeholder(width: int, height: int, text: str, font) -> Image.Image:
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = max(0, (width - (bbox[2] - bbox[0])) // 2)
    draw.text((x, (height - 20) // 2), text, font=font, fill="#ff6666")
    return img


def apply_display_shift(img: Image.Image, *, width: int, height: int, display_x_shift: int) -> Image.Image:
    if display_x_shift == 0:
        return img
    shifted = Image.new("RGB", (width, height), "black")
    shifted.paste(img, (display_x_shift, 0))
    return shifted


def rgb888_to_rgb565_bytes(img: Image.Image, *, width: int, height: int) -> bytearray:
    """Convert RGB888 image data into little-endian RGB565 bytes.

    This stays intentionally dependency-free so the app does not need numpy
    resident in memory on constrained devices.
    """

    if img.mode != "RGB":
        img = img.convert("RGB")

    global _RGB565_BUFFER

    raw = memoryview(img.tobytes())
    required_size = width * height * 2
    if len(_RGB565_BUFFER) != required_size:
        _RGB565_BUFFER = bytearray(required_size)
    out = _RGB565_BUFFER
    src = 0
    dst = 0
    for _ in range(width * height):
        r, g, b = raw[src], raw[src + 1], raw[src + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[dst] = v & 0xFF
        out[dst + 1] = v >> 8
        src += 3
        dst += 2
    return out


def write_framebuffer(img: Image.Image, *, config: DisplayAdapterConfig) -> None:
    global _LAST_FRAME_HASH
    raw = rgb888_to_rgb565_bytes(
        apply_display_shift(img, width=config.width, height=config.height, display_x_shift=config.display_x_shift),
        width=config.width,
        height=config.height,
    )

    frame_hash = hashlib.blake2b(raw, digest_size=16).digest()
    if frame_hash == _LAST_FRAME_HASH:
        return

    try:
        with open(config.fb_device, "wb", buffering=0) as fb:
            fb.write(raw)
    except PermissionError as exc:
        raise PermissionError(
            f"Permission denied writing to {config.fb_device}. Need root or video group membership."
        ) from exc

    _LAST_FRAME_HASH = frame_hash


def try_write_framebuffer(
    img: Image.Image,
    *,
    context: str,
    config: DisplayAdapterConfig,
    logger: DisplayAdapterLogger,
) -> bool:
    try:
        write_framebuffer(img, config=config)
        return True
    except Exception as exc:
        logger.log_exception("framebuffer", f"Failed during {context}", exc)
        return False


def write_fallback_placeholder(
    text: str,
    *,
    context: str,
    font,
    config: DisplayAdapterConfig,
    logger: DisplayAdapterLogger,
) -> None:
    img = create_error_placeholder(config.width, config.height, text, font)
    if not try_write_framebuffer(
        img,
        context=f"{context} placeholder",
        config=config,
        logger=logger,
    ):
        logger.log_message("framebuffer", f"Unable to display fallback placeholder for: {context}")
