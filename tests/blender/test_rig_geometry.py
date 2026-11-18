# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys
from pathlib import Path
import math
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'lichtfeld_training'))
from rig_geometry import directions, sample_path, guide_path, seeded_offset, station_directions, repeated_poses, panorama_views_for_step
from geometry_maps import convert


class GeometryTests(unittest.TestCase):
    def test_panorama_density_counts_full_turn_and_partial_endpoints(self):
        step = math.radians(30)
        for sweep, expected in ((math.tau, 12), (math.pi, 7), (math.pi / 6, 4)):
            with self.subTest(sweep=sweep):
                count = panorama_views_for_step(sweep, step)
                self.assertEqual(count, expected)
                row = list(station_directions(count, 'BAND', 0, sweep, 0, 0))
                pairs = list(zip(row, row[1:]))
                if sweep == math.tau:
                    pairs.append((row[-1], row[0]))
                for a, b in pairs:
                    dot = sum(x * y for x, y in zip(a, b))
                    self.assertLessEqual(math.acos(min(1, dot)), step + 1e-12)
        for step in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                panorama_views_for_step(math.pi, step)

    def test_panorama_density_tolerates_rna_rounding_at_exact_divisors(self):
        for degrees, step, expected in ((360, 10, 36), (360, 20, 18), (360, 40, 9),
                                        (180, 10, 19), (180, 20, 10), (180, 30, 7),
                                        (180, 19.999, 11)):
            with self.subTest(sweep=degrees, step=step):
                sweep, maximum_step = (float(np.float32(math.radians(v))) for v in (degrees, step))
                self.assertEqual(panorama_views_for_step(sweep, maximum_step), expected)

    def test_repeated_poses_keep_orientation_roll_and_first_representative(self):
        def run(frames):
            work=repeated_poses(frames)
            while True:
                try:next(work)
                except StopIteration as done:return done.value
        base=((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1))
        rolled=((-1,0,0,0),(0,-1,0,0),(0,0,1,0),(0,0,0,1))
        shifted=((1,0,0,.00001),(0,1,0,0),(0,0,1,0),(0,0,0,1))
        self.assertEqual(run([base,rolled,base,shifted,base,rolled]),[(0,2),(0,4),(1,5)])
        self.assertEqual(run([]),[])

    def test_ordered_station_rows_have_matching_azimuths(self):
        for rows in (1,2,3,7):
            for sweep in (math.pi,2*math.pi):
                points=list(station_directions(8,'GRID',.2,sweep,-60,60,rows=rows))
                self.assertEqual(len(points),8*rows)
                self.assertEqual(len(set(points)),len(points))
                for row in range(rows):
                    group=points[row*8:(row+1)*8]
                    self.assertTrue(all(abs(p[2]-group[0][2])<1e-12 for p in group))
                    self.assertTrue(np.allclose([math.atan2(p[1],p[0]) for p in group],
                                                [math.atan2(p[1],p[0]) for p in points[:8]]))
                self.assertTrue(all(abs(p[2])<1 for p in points))
        with self.assertRaisesRegex(ValueError,'Elevation rows'):
            list(station_directions(8,'GRID',0,2*math.pi,-90,90))

    def test_station_coverage_domains_and_legacy_output(self):
        for count in (4, 8, 17, 1000):
            for shape in ('BAND', 'ELEVATION', 'HEMISPHERE', 'SPHERE'):
                points=list(station_directions(count,shape,0,math.pi,-30,60,centered=True))
                self.assertEqual(len(set(points)),count)
                self.assertTrue(all(abs(math.dist((0,0,0),p)-1)<1e-12 for p in points))
                if shape=='HEMISPHERE':
                    self.assertTrue(all(p[0]>0 for p in points))
                if shape=='ELEVATION':
                    self.assertTrue(all(-.5<p[2]<math.sin(math.pi/3) for p in points))
                    self.assertTrue(all(abs(math.atan2(p[1],p[0]))<=math.pi/2 for p in points))
                if shape=='SPHERE':
                    self.assertTrue(any(p[2]<0 for p in points) and any(p[2]>0 for p in points))
                    self.assertTrue(all(abs(p[2])<1 for p in points))
            self.assertEqual(list(station_directions(count,'BAND',.3,math.pi,-30,60)),
                             list(directions(count,'RINGS',0,0,1,.3,sweep=math.pi)))
            self.assertEqual(list(station_directions(count,'ELEVATION',.3,2*math.pi,-90,90)),
                             list(directions(count,'SPHERE',phase=.3)))
        for mode in ('SPHERE','HEMISPHERE'):
            self.assertEqual(list(directions(17,mode,phase=.3)),
                             list(directions(17,mode,phase=.3,sweep=float(np.float32(2*math.pi)))))
        for minimum,maximum in ((20,20),(45,-45),(float('nan'),45)):
            with self.assertRaises(ValueError):
                list(station_directions(8,'ELEVATION',0,math.pi,minimum,maximum))

    def test_position_variation_is_bounded_stable_and_independent(self):
        self.assertTrue(np.allclose(seeded_offset(3,42,.5),
                         (0.4057713892178399, -0.20126827061603134, -0.004931187225363089),
                         rtol=0, atol=1e-15))
        angular = [seeded_offset(i,42,.05,stream=1) for i in range(100)]
        self.assertTrue(all(math.dist((0,0,0),p) <= .05 for p in angular))
        self.assertNotEqual(angular, [seeded_offset(i,42,.05) for i in range(100)])
        import random
        before = random.getstate()
        pattern = [seeded_offset(i, 42, .5) for i in range(100)]
        self.assertEqual(before, random.getstate())
        self.assertEqual(pattern, [seeded_offset(i, 42, .5) for i in range(100)])
        self.assertTrue(all(math.dist((0,0,0),p) <= .5 for p in pattern))
        self.assertNotEqual(pattern, [seeded_offset(i, 43, .5) for i in range(100)])
        for i,p in enumerate(pattern):
            self.assertEqual(seeded_offset(i,42,0), (0,0,0))
            self.assertTrue(np.allclose(seeded_offset(i,42,1), np.array(p)*2))
            self.assertTrue(np.allclose(seeded_offset(i,42,.5,math.pi/2), (-p[1],p[0],p[2])))
        for amplitude in (-1,float('nan'),float('inf')):
            with self.subTest(amplitude=amplitude),self.assertRaises(ValueError):
                seeded_offset(0,42,amplitude)

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
    def test_ring_sweep_sectors_and_full_turn_seam(self):
        # The existing full-turn path retains its exact arithmetic.
        for count in (4,8,16):
            actual=list(directions(count,'RINGS',0,0,1,.3))
            expected=[(math.cos(.3+2*math.pi*i/count),math.sin(.3+2*math.pi*i/count),0) for i in range(count)]
            self.assertEqual(actual,expected)
            for degrees in (120,180,270,360):
                sweep=math.radians(degrees)
                points=list(directions(count,'RINGS',0,0,1,.3,sweep=sweep))
                self.assertEqual(len(set(points)),count)
                divisor=count if degrees==360 else count-1
                self.assertTrue(np.allclose(points,[(math.cos(.3+sweep*i/divisor),math.sin(.3+sweep*i/divisor),0) for i in range(count)]))
        rounded=float(np.float32(2*math.pi))
        self.assertEqual(list(directions(8,'RINGS',0,0,1,.3,sweep=rounded)),list(directions(8,'RINGS',0,0,1,.3)))
        for sweep in (0,-1,float('nan'),float('inf'),7):
            with self.subTest(sweep=sweep),self.assertRaises(ValueError):
                list(directions(8,'RINGS',0,0,1,sweep=sweep))

    def test_centered_sectors_keep_facing_and_full_turn_start(self):
        points=list(directions(5,'RINGS',0,0,1,sweep=math.pi,centered=True))
        self.assertTrue(np.allclose(points[2],(1,0,0)))
        self.assertTrue(np.allclose(points[0],(0,-1,0)))
        self.assertTrue(np.allclose(points[-1],(0,1,0)))
        self.assertEqual(list(directions(8,'RINGS',0,0,1,.3,centered=True)),
                         list(directions(8,'RINGS',0,0,1,.3)))

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

    def test_maximum_spacing_includes_open_endpoints_and_closed_seam(self):
        points = [(0,0,0), (10,0,0)]
        samples = list(sample_path(points, 48, spacing=3))
        self.assertTrue(np.allclose(samples, [(0,0,0),(2.5,0,0),(5,0,0),(7.5,0,0),(10,0,0)]))
        square = [(0,0,0),(1,0,0),(1,1,0),(0,1,0)]
        self.assertEqual(list(sample_path(square, 48, True, spacing=1)), square)
        for spacing in (0, -1, float('nan'), float('inf'), .001, 100):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                list(sample_path(points, 48, spacing=spacing))

    def test_regular_guides_have_expected_dimensions_and_no_duplicate_seam(self):
        orbit = guide_path('ORBIT', 2, 0, 2*math.pi, 3, 0, 1, 0)[0]
        self.assertEqual(len(orbit), 72)
        self.assertEqual(len(set(orbit)), len(orbit))
        self.assertTrue(all(abs(math.dist(p, (0,0,0))-2) < 1e-10 for p in orbit))
        arc = guide_path('ORBIT', 2, 0, math.pi/2, 3, 0, 1, 0)[0]
        self.assertTrue(np.allclose(arc[0], (2,0,0)))
        self.assertTrue(np.allclose(arc[-1], (0,2,0)))
        helix = guide_path('HELIX', 2, 0, 2*math.pi, 2, 0, 1, -4)[0]
        self.assertTrue(np.allclose(helix[-1], (2,0,-4)))
        spiral = guide_path('SPIRAL', 2, 0, 2*math.pi, 3, 0, math.pi/2, 0)[0]
        self.assertTrue(np.allclose(spiral[-1], (0,0,2)))
        self.assertTrue(np.allclose(np.diff([p[2] for p in spiral]), 2/(len(spiral)-1)))
        self.assertTrue(all(abs(math.dist(p, (0,0,0))-2) < 1e-10 for p in spiral))
        with self.assertRaises(ValueError):
            list(guide_path('SPIRAL', 2, 0, 2*math.pi, 3, 1, 0, 0))
        with self.assertRaises(ValueError):
            guide_path('HELIX', 2, 0, 2*math.pi, 3, 0, 1, 0)
        polar, closed = guide_path('SPIRAL', 2, 0, 2*math.pi, .1, -math.pi/2, math.pi/2, 0)
        self.assertFalse(closed)
        elevation = [math.asin(p[2]/2) for p in polar]
        self.assertLessEqual(max(np.diff(elevation)), math.radians(5)+1e-10)

    def test_training_parameter_conflicts(self):
        from training_options import validate
        self.assertTrue(validate('mcmc',{'shs_lr':-1}))
        self.assertFalse(validate('mrnf',{'shs_lr':-1,'grow_fraction':-1}))
        self.assertTrue(validate('mrnf',{'lambda_dssim':1.1}))
        self.assertTrue(validate('mrnf',{'use_exposure_correction':True,'use_ppisp':True}))
