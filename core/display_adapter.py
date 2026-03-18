"""Framebuffer output helpers and fallback display handling."""

from dataclasses import dataclass
import sys
from typing import Callable

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class DisplayAdapterConfig:
    fb_device: str
    width: int
    height: int
    display_x_shift: int


@dataclass(frozen=True)
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


def rgb888_to_rgb565_bytes(img: Image.Image, *, width: int, height: int) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    raw = img.tobytes()
    out = bytearray(width * height * 2)
    for i in range(width * height):
        r, g, b = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i * 2] = v & 0xFF
        out[i * 2 + 1] = v >> 8
    return bytes(out)


def write_framebuffer(img: Image.Image, *, config: DisplayAdapterConfig) -> None:
    try:
        raw = rgb888_to_rgb565_bytes(
            apply_display_shift(img, width=config.width, height=config.height, display_x_shift=config.display_x_shift),
            width=config.width,
            height=config.height,
        )
        with open(config.fb_device, "wb", buffering=0) as fb:
            fb.write(raw)
    except PermissionError:
        print(f"[framebuffer] Permission denied writing to {config.fb_device}. Need root or video group membership.", file=sys.stderr)


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