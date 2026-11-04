# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lichtfeld_training'))
import capture_settings as capture


def settings(**kwargs):
    values = dict(capture_format='PRESET', capture_aspect='WIDE', capture_orientation='LANDSCAPE',
                  resolution=1024, capture_width=333, capture_height=777, override_optics=False,
                  override_shift=False, override_clipping=False)
    return NS(**(values | kwargs))


class CaptureSettings(unittest.TestCase):
    def setUp(self):
        self.scene = NS(render=NS(resolution_x=1920, resolution_y=1080, resolution_percentage=50,
                                  pixel_aspect_x=1., pixel_aspect_y=1.3))

    def test_presets_portrait_rounding_and_square_pixels(self):
        for key, (x, y) in capture.ASPECTS.items():
            for orientation in ('LANDSCAPE', 'PORTRAIT'):
                value = capture.preset_format(settings(capture_aspect=key, capture_orientation=orientation))
                ratio = y/x if orientation == 'PORTRAIT' else x/y
                self.assertLess(abs(value['width'] / value['height'] - ratio), .004)
                self.assertEqual(max(value['width'], value['height']), 1024)
                self.assertEqual(value['pixel_aspect_x'], value['pixel_aspect_y'])

    def test_scene_percentage_pixels_and_custom(self):
        self.assertEqual(capture.format_for(self.scene, settings(capture_format='SCENE')),
                         dict(width=960, height=540, pixel_aspect_x=1., pixel_aspect_y=1.3))
        value = capture.preset_format(settings(capture_format='CUSTOM'))
        self.assertEqual((value['width'], value['height']), (333, 777))
        self.assertEqual(capture.preset_format(settings(capture_format='CUSTOM'), mode='PRESET')['width'], 1024)
        self.assertEqual(capture.format_for(self.scene, settings(capture_format='CUSTOM')),
                         dict(width=960, height=540, pixel_aspect_x=1., pixel_aspect_y=1.3))

    def test_no_silent_resize_or_invalid_numeric_values(self):
        for number in (0, 63, 4097, True, 100., float('nan')):
            with self.assertRaises(ValueError):
                capture.validate_format(dict(width=number, height=100, pixel_aspect_x=1, pixel_aspect_y=1))
        self.scene.render.resolution_x = 9000
        with self.assertRaises(ValueError):
            capture.format_for(self.scene, settings(capture_format='SCENE'))

    def test_camera_groups_opt_in_and_invalid_clipping(self):
        self.assertEqual(capture.camera_for(settings()), {})
        s = settings(override_shift=True, capture_shift_x=.1, capture_shift_y=-.2)
        self.assertEqual(capture.camera_for(s), dict(shift_x=.1, shift_y=-.2))
        for value in ({'clip_start':1}, {'clip_start':2, 'clip_end':1}, {'lens':float('nan')}, {'unknown':1}):
            with self.assertRaises(ValueError):
                capture.validate_camera(value)

    def test_legacy_jobs_keep_original_pixel_aspect(self):
        self.assertEqual(capture.job_format({'resolution':128}, self.scene),
                         dict(width=128, height=128, pixel_aspect_x=1., pixel_aspect_y=1.3))
        explicit = dict(width=160, height=90, pixel_aspect_x=1, pixel_aspect_y=1)
        self.assertEqual(capture.job_format({'resolution':128, 'capture_format':explicit}, self.scene), explicit)


if __name__ == '__main__':
    unittest.main()
