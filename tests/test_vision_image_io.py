# -*- coding: utf-8 -*-
"""图片读取边界：远程路径、超限、损坏、越界。"""
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from blackjack_lab.vision.contracts import LayoutProfile, RegionBox, default_layout
from blackjack_lab.vision.deps import ImageRejected
from blackjack_lab.vision.image_io import load_image, read_png_rgb, write_png_rgb
from blackjack_lab.vision.synthetic import RgbCanvas, FELT


def _png_header(width: int, height: int) -> bytes:
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class TestImageIo(unittest.TestCase):
    def test_roundtrip_png(self):
        canvas = RgbCanvas(4, 3, FELT)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            canvas.save_png(path)
            loaded = load_image(path)
            self.assertEqual((loaded.width, loaded.height), (4, 3))
            self.assertEqual(len(loaded.sha256), 64)

    def test_reject_http(self):
        with self.assertRaises(ImageRejected):
            load_image("https://example.invalid/card.png")

    def test_reject_missing(self):
        with self.assertRaises(ImageRejected):
            load_image(Path("C:/definitely-missing-hakimi-vision.png"))

    def test_reject_oversize_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.png"
            path.write_bytes(_png_header(10000, 10000))
            with self.assertRaises(ImageRejected):
                load_image(path)

    def test_reject_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
            with self.assertRaises(ImageRejected):
                load_image(path)

    def test_reject_oversize_file_limit(self):
        layout = default_layout()
        tiny = LayoutProfile(
            layout_profile_id=layout.layout_profile_id,
            style_id=layout.style_id,
            canvas_width=layout.canvas_width,
            canvas_height=layout.canvas_height,
            regions=layout.regions,
            max_image_bytes=8,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.png"
            write_png_rgb(path, 1, 1, b"\x00\x00\x00")
            with self.assertRaises(ImageRejected):
                load_image(path, tiny)

    def test_stdlib_png_reader(self):
        rgb = b"\xff\x00\x00" * 2 + b"\x00\xff\x00" * 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.png"
            write_png_rgb(path, 2, 2, rgb)
            w, h, out = read_png_rgb(path.read_bytes())
            self.assertEqual((w, h, out), (2, 2, rgb))


if __name__ == "__main__":
    unittest.main()
