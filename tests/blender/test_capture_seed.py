# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import tempfile
import unittest
import io,zipfile
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lichtfeld_training'))
import capture_seed as seed
from rig_mesh import calculate
from geometry_maps import unproject_depth,write_float_tiff,read_depth_tiff


class VisibleSeedContracts(unittest.TestCase):
    def test_pixel_stratification_preserves_valid_ranks_with_empty_rows(self):
        valid=np.random.default_rng(10).random((51,47))>.3;valid[5:10]=False
        full=np.flatnonzero(valid)
        ranks=((np.arange(57)+np.random.default_rng(142860).random(57))*len(full)/57).astype(np.int64)
        np.testing.assert_array_equal(seed.pixel_indices(valid,57,3),full[ranks])
        self.assertEqual(len(seed.pixel_indices(np.zeros((10,10),bool),57)),0)
    def test_surface_instance_plan_never_exceeds_budget_and_covers_large_sets(self):
        for limit,instances in ((1,1),(8,4),(17,8),(100,300),(30000,1000000)):
            plan=list(seed.instance_sample_plan(limit,instances))
            self.assertLessEqual(sum(take for _,take in plan),limit)
            self.assertEqual(len(plan),min(limit,instances))
            self.assertEqual(len({index for index,_ in plan}),len(plan))
            self.assertTrue(all(take>=1 for _,take in plan))
            if instances>limit:
                indexes=[index for index,_ in plan]
                gaps=np.diff(indexes)
                self.assertLessEqual(int(gaps.max()),(instances+limit-1)//limit)
    def test_depth_unprojection_and_float_tiff_preserve_shift_aspect_and_origin(self):
        depth=np.full((19,31),3,np.float32)
        frame=dict(fl_x=45.,fl_y=23.,cx=12.,cy=7.)
        matrix=np.array([[0,-1,0,13],[1,0,0,17],[0,0,1,19],[0,0,0,1]],float)
        xyz=unproject_depth(depth,matrix,frame)
        rgba=np.full((19,31,4),255,np.uint8)
        points,_=seed.samples(xyz,depth,rgba,matrix,frame,1000)
        self.assertEqual(len(points),19*31)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'depth.tiff';write_float_tiff(path,depth)
            np.testing.assert_array_equal(read_depth_tiff(path,31,19),depth[::-1])
            with self.assertRaises(ValueError):read_depth_tiff(path,30,19)
    def test_shifted_asymmetric_camera_uses_pixel_correspondences(self):
        height, width = 19, 31
        frame = dict(fl_x=45., fl_y=23., cx=12., cy=7.)
        rows, cols = np.indices((height, width))
        local = np.stack(((cols+.5-frame['cx'])/frame['fl_x']*3,
                          -(height-rows-.5-frame['cy'])/frame['fl_y']*3, np.full((height,width),-3)), axis=-1)
        matrix = np.array([[0,-1,0,3],[1,0,0,5],[0,0,1,7],[0,0,0,1]], dtype=float)
        position = local @ matrix[:3,:3].T + matrix[:3,3]
        rgba = np.zeros((height,width,4),np.uint8);rgba[:]=[127,51,200,255]
        points, colors = seed.samples(position, np.full((height,width),3.), rgba, matrix, frame, 1000)
        np.testing.assert_allclose(points, position.reshape(-1,3), atol=1e-6)
        np.testing.assert_array_equal(colors, np.tile([127,51,200],(height*width,1)))
        rgba[:]=0
        self.assertEqual(len(seed.samples(position, np.full((height,width),3.),rgba,matrix,frame,1000)[0]),0)
        rgba[:]=255
        position += [100,100,100]
        self.assertEqual(len(seed.samples(position,np.full((height,width),3.),rgba,matrix,frame,1000)[0]),0)

    def test_global_budget_duplicates_and_recovery_preserve_color(self):
        points=np.array([[x,y,z] for x in range(5) for y in range(5) for z in range(5)],np.float32)
        colors=np.tile(np.array([127,51,200],np.uint8),(len(points),1))
        with tempfile.TemporaryDirectory() as root:
            for index in range(4):seed.save_view(root,index,points,colors)
            result=seed.finalize(root,32,4)
            self.assertEqual(result['points'],32)
            path=Path(root)/'pointcloud.ply';before=path.read_bytes()
            seed.finalize(root,32,4)
            self.assertEqual(before,path.read_bytes())
            data=np.frombuffer(before.split(b'end_header\n',1)[1],dtype=[('xyz','<f4',(3,)),('rgb','u1',(3,))])
            np.testing.assert_array_equal(data['rgb'],np.tile([127,51,200],(32,1)))
            np.savez(Path(root)/'capture_points/view_000000.npz',points=np.zeros((20000,3)),colors=colors)
            with self.assertRaises(ValueError):seed.load_view(root,0,256)
            bad=io.BytesIO()
            np.lib.format.write_array_header_1_0(bad,{'descr':'<f4','fortran_order':False,'shape':(2**40,3)})
            with zipfile.ZipFile(Path(root)/'capture_points/view_000000.npz','w') as archive:
                archive.writestr('points.npy',bad.getvalue())
                archive.writestr('colors.npy',b'invalid')
            with self.assertRaises(ValueError):seed.load_view(root,0,256)
        self.assertLessEqual(seed.view_budget(30000,1000)*1000,4*30000+256000)

    def test_mesh_normals_use_inverse_transpose_and_original_coordinates(self):
        centers=np.array([[1,0,0],[-1,0,0],[0,1,0],[0,0,1]],float)
        normals=centers.copy();normals[0]=[1,1,0]
        matrix=np.diag([2.,3.,4.,1.]);matrix[:3,3]=[13,17,19]
        result=calculate(centers,np.array([[0,1,2]]),np.array([0]),centers,normals,matrix,8,'FACES','INWARD',.1)
        expected=np.array([.5,1/3,0]);expected/=np.linalg.norm(expected)
        np.testing.assert_allclose(result[0][1],-expected)
        np.testing.assert_allclose(result[0][0],np.array([15,17,19])+.1*expected)
        matrix[0,0]=0
        with self.assertRaises(ValueError):calculate(centers,np.array([[0,1,2]]),np.array([0]),centers,normals,matrix,8,'FACES','INWARD',0)


if __name__=='__main__':unittest.main()
