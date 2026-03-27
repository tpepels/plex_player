import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from core.display_adapter import DisplayAdapterConfig, DisplayAdapterLogger, try_write_framebuffer


class DisplayAdapterTests(unittest.TestCase):
    def test_try_write_framebuffer_logs_permission_error(self):
        captured = []
        logger = DisplayAdapterLogger(
            log_exception=lambda component, context, exc: captured.append((component, context, exc)),
            log_message=lambda *_args: None,
        )
        config = DisplayAdapterConfig(fb_device="/dev/fb-test", width=1, height=1, display_x_shift=0)
        img = Image.new("RGB", (1, 1), "black")

        with patch("core.display_adapter.open", side_effect=PermissionError("denied")):
            ok = try_write_framebuffer(img, context="unit-test", config=config, logger=logger)

        self.assertFalse(ok)
        self.assertEqual(len(captured), 1)
        component, context, exc = captured[0]
        self.assertEqual(component, "framebuffer")
        self.assertEqual(context, "Failed during unit-test")
        self.assertIn("/dev/fb-test", str(exc))

    def test_try_write_framebuffer_returns_true_on_success(self):
        captured = []
        logger = DisplayAdapterLogger(
            log_exception=lambda component, context, exc: captured.append((component, context, exc)),
            log_message=lambda *_args: None,
        )
        img = Image.new("RGB", (1, 1), "black")

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name

        try:
            config = DisplayAdapterConfig(fb_device=path, width=1, height=1, display_x_shift=0)
            ok = try_write_framebuffer(img, context="unit-test", config=config, logger=logger)
            self.assertTrue(ok)
            self.assertEqual(captured, [])
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
