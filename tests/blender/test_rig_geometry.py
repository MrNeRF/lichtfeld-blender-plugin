# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
import math
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'lichtfeld_training'))
from rig_geometry import directions, sample_path
from geometry_maps import convert


class GeometryTests(unittest.TestCase):
    def test_unique_every_count_including_old_overlap_counts(self):
        for mode in ('SPHERE','HEMISPHERE','RINGS'):
            for count in (4,8,9,16,48,96,144,1000):
                points=list(directions(count,mode,0,80,min(3,count//4)))
                self.assertEqual(len(points),count)
                self.assertEqual(len({tuple(round(x,10) for x in p) for p in points}),count)
                for point in points:
                    self.assertAlmostEqual(math.dist((0,0,0),point),1)
    def test_equal_area_cap_and_full_sphere(self):
        points=list(directions(100,'HEMISPHERE',10,70))
        z=[p[2] for p in points]
        self.assertTrue(all(math.sin(math.radians(10))<v<math.sin(math.radians(70)) for v in z))
        self.assertTrue(np.allclose(np.diff(z),np.diff(z)[0]))
        self.assertAlmostEqual(sum(p[2] for p in directions(100,'SPHERE')),0)
    def test_invalid_distributions(self):
        for args in ((8,'HEMISPHERE',80,0),(8,'RINGS',0,80,3),(8,'RINGS',0,0,2)):
            with self.assertRaises(ValueError):list(directions(*args))
    def test_arc_distance_and_closed_endpoint(self):
        result=list(sample_path([(0,0,0),(1,0,0),(1,9,0)],6))
        self.assertTrue(np.allclose(result,[(0,0,0),(1,1,0),(1,3,0),(1,5,0),(1,7,0),(1,9,0)]))
        result=list(sample_path([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],4,True))
        self.assertEqual(len(set(result)),4)
        with self.assertRaises(ValueError):list(sample_path([(0,0,0)]*2,8))
    def test_rotated_translated_camera_metric_depth(self):
        matrix=np.array([[0,0,1,7],[0,1,0,-2],[-1,0,0,.5],[0,0,0,1.]])
        position=np.array([[[3,-2,.5],[np.nan,0,0]]])
        normal=np.array([[[1.,0,0],[0,0,0]]])
        depth,n,valid=convert(position,normal,matrix)
        self.assertAlmostEqual(depth[0,0],4)
        self.assertTrue(np.allclose(n[0,0],[.5,.5,0]))
        self.assertFalse(valid[0,1])
        self.assertEqual(depth[0,1],0)

    def test_training_parameter_conflicts(self):
        from training_options import validate
        self.assertTrue(validate('mcmc',{'shs_lr':-1}))
        self.assertFalse(validate('mrnf',{'shs_lr':-1,'grow_fraction':-1}))
        self.assertTrue(validate('mrnf',{'lambda_dssim':1.1}))
        self.assertTrue(validate('mrnf',{'use_exposure_correction':True,'use_ppisp':True}))
