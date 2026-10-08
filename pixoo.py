"""Divoom Pixoo Max Bluetooth protocol.

Ported from https://github.com/jakobwesthoff/divoom-pixoo-max-nodejs
"""

import logging
import re
import socket
import subprocess
import time

SIZE = 32

log = logging.getLogger("pixoo")


def _u16(v: int) -> bytes:
    return (v & 0xFFFF).to_bytes(2, "little")


def make_message(payload: bytes) -> bytes:
    length = _u16(len(payload) + 2)  # + checksum
    checksum = _u16(sum(length + payload))
    return b"\x01" + length + payload + checksum + b"\x02"


def msg_brightness(percent: int) -> bytes:
    return make_message(bytes([0x74, max(0, min(100, percent))]))


def msg_static_image(rgb: bytes) -> bytes:
    """rgb is 32*32*3 bytes, row by row."""
    palette: dict[bytes, int] = {}
    screen = []
    for i in range(0, len(rgb), 3):
        color = rgb[i : i + 3]
        idx = palette.setdefault(color, len(palette))
        screen.append(idx)

    bits = max(1, (len(palette) - 1).bit_length())
    acc = nbits = 0
    screen_buf = bytearray()
    for idx in screen:  # palette indices, packed little-endian, `bits` bits each
        acc |= idx << nbits
        nbits += bits
        while nbits >= 8:
            screen_buf.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    if nbits:
        screen_buf.append(acc & 0xFF)

    color_buf = b"".join(palette)
    frame_time = _u16(0)
    palette_type = b"\x03"  # Pixoo Max palette, up to 1024 colors
    palette_count = _u16(len(palette))
    frame_size = _u16(2 + len(frame_time) + 1 + 2 + len(color_buf) + len(screen_buf))
    payload = (
        bytes([0x44, 0x00, 0x0A, 0x0A, 0x04, 0xAA])
        + frame_size
        + frame_time
        + palette_type
        + palette_count
        + color_buf
        + bytes(screen_buf)
    )
    return make_message(payload)


class Pixoo:
    def __init__(self, addr: str, channel: int, brightness: int | None = None):
        self.addr = addr
        self.channel = channel
        self.brightness = brightness
        self.sock: socket.socket | None = None

    def connect(self):
        self.close()
        log.info(f"connecting to {self.addr} (channel {self.channel})…")
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(10)
        s.connect((self.addr, self.channel))
        self.sock = s
        # Without a pause after connecting the display ignores or truncates the first commands.
        time.sleep(0.2)
        log.info("connected")
        if self.brightness is not None:
            s.sendall(msg_brightness(self.brightness))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _drain(self):
        # The display sends replies; drain them so the receive buffer doesn't fill up.
        assert self.sock
        self.sock.setblocking(False)
        try:
            while self.sock.recv(1024):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            self.sock.settimeout(10)

    def send(self, data: bytes, retries: int = 5):
        for attempt in range(retries):
            try:
                if not self.sock:
                    self.connect()
                assert self.sock
                self._drain()
                self.sock.sendall(data)
                return
            except OSError as e:
                log.warning(f"error ({e}), reconnecting…")
                self.close()
                time.sleep(2 * (attempt + 1))
        raise ConnectionError("failed to send data to the Pixoo")

    def show(self, rgb: bytes):
        self.send(msg_static_image(rgb))


def find_pixoo_addr() -> str | None:
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.*)", line.strip(), re.I)
        if m and "pixoo" in m.group(2).lower():
            return m.group(1)
    return None
