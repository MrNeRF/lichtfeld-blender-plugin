# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'lichtfeld_training'))
from ui_text import wrap_pixels
class TextWrapping(unittest.TestCase):
 def test_width_uses_real_measurement_instead_of_fixed_character_limit(self):
  text='Use Trainer color view to compare colors with LichtFeld. The normal Blender viewport uses different shading.'
  narrow=wrap_pixels(text,120,lambda s:len(s)*6)
  wide=wrap_pixels(text,440,lambda s:len(s)*6)
  self.assertLess(len(wide),len(narrow))
  self.assertGreater(len(wide[0]),32)
  self.assertEqual(' '.join(wide),text)
 def test_long_paths_and_variable_width_fonts_fit(self):
  measure=lambda s:sum(9 if c=='W' else 3 for c in s)
  lines=wrap_pixels('WWWWWWWWWWWWW C:/folder/folder/folder/project.blend',36,measure)
  self.assertTrue(all(measure(s)<=36 for s in lines))
if __name__=='__main__':unittest.main()
