#!/usr/bin/env python3
"""Source-level regression checks for the Android 17 PE legacy toast port.

These tests check the actual framework resource XML used by the Android build.
They do not replace aapt2, an Android image build or device rendering tests.
"""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
RES = ROOT / "core/res/res"


def xml(relative: str) -> ET.Element:
    return ET.parse(RES / relative).getroot()


class PixelExperienceToastResourceTest(unittest.TestCase):
    def test_both_toast_layouts_use_same_aosp17_rounded_frame(self):
        for path in (
            "layout/transient_notification.xml",
            "layout/transient_notification_with_icon.xml",
        ):
            with self.subTest(layout=path):
                root = xml(path)
                self.assertEqual(
                    "@android:drawable/toast_frame",
                    root.get(ANDROID_NS + "background"),
                )
                self.assertEqual("wrap_content", root.get(ANDROID_NS + "layout_width"))
                self.assertEqual("wrap_content", root.get(ANDROID_NS + "layout_height"))
                self.assertEqual("@dimen/toast_width", root.get(ANDROID_NS + "maxWidth"))

    def test_reused_existing_android17_frame_is_rounded(self):
        frame = xml("drawable/toast_frame.xml")
        self.assertEqual("shape", frame.tag)
        corners = frame.find("corners")
        self.assertIsNotNone(corners)
        self.assertEqual("28dp", corners.get(ANDROID_NS + "radius"))
        # Keep the AOSP17 theme-aware surface color; don't reintroduce
        # the old PE Android14 colorBackground drawable.
        solid = frame.find("solid")
        self.assertIsNotNone(solid)
        self.assertEqual("?android:attr/colorSurface", solid.get(ANDROID_NS + "color"))

    def test_layout_content_is_preserved(self):
        plain = xml("layout/transient_notification.xml")
        icon = xml("layout/transient_notification_with_icon.xml")
        self.assertIsNotNone(plain.find("TextView"))
        self.assertIsNotNone(icon.find("TextView"))
        self.assertIsNotNone(icon.find("ImageView"))
        self.assertEqual(
            "@android:id/message",
            plain.find("TextView").get(ANDROID_NS + "id"),
        )


if __name__ == "__main__":
    unittest.main()
