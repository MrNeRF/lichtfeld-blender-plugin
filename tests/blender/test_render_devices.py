# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device contracts must never silently select a different renderer or CPU."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lichtfeld_training'))
import render_devices as devices
import scene_render_worker as worker


def request():
    return {'engine':'CYCLES', 'mode':'GPU', 'backend':'OPTIX',
            'devices':[{'name':'GPU A','type':'OPTIX','id':'gpu-a'}], 'options':{'peer_memory':False}}


class RenderDevices(unittest.TestCase):
    def test_graphics_selection_and_strict_device_flag(self):
        value = {'engine':'BLENDER_EEVEE', 'mode':'GRAPHICS', 'backend':'VULKAN', 'graphics_device':'10de/2d04/0'}
        self.assertEqual(devices.arguments(value), ['--gpu-backend','vulkan','--gpu-device','10de/2d04/0','--gpu-device-no-fallback'])
        self.assertEqual(devices.arguments(request()), [])
        for key, invalid in [('graphics_device','--gpu-device 0'), ('graphics_device',None), ('backend','OPENGL')]:
            with self.assertRaises(ValueError): devices.validate(value | {key:invalid})

    def test_cpu_gpu_and_mixed_backend_choices(self):
        self.assertEqual(devices.validate({'engine':'CYCLES','mode':'CPU','backend':'CPU'})['mode'], 'CPU')
        value = request()
        value['devices'].append({'name':'CPU A','type':'CPU','id':'cpu-a'})
        self.assertEqual(len(devices.validate(value)['devices']), 2)
        for invalid in [[], [{'name':'AMD','type':'HIP','id':'amd'}], [{'name':'CPU','type':'CPU','id':'cpu'}]]:
            with self.assertRaises(ValueError): devices.validate(request() | {'devices':invalid})
        duplicate = request()['devices'] * 2
        with self.assertRaises(ValueError): devices.validate(request() | {'devices':duplicate})

    def test_malformed_inputs_are_rejected_without_driver_calls(self):
        for value in [None, [], {'engine':[]}, request() | {'backend':[]}, request() | {'devices':[None]},
                      request() | {'options':{'peer_memory':1}}, request() | {'options':{'metalrt':[]}},
                      request() | {'options':{'unmanaged':True}}]:
            with self.assertRaises(ValueError): devices.validate(value)

    def test_requested_gpu_selection_does_not_enable_other_devices(self):
        selected = NS(id='gpu-a',name='GPU A',type='OPTIX',use=False,meets_driver_requirement=True)
        other = NS(id='gpu-b',name='GPU B',type='OPTIX',use=True,meets_driver_requirement=True)
        cpu = NS(id='cpu',name='CPU',type='CPU',use=True,meets_driver_requirement=True)
        prefs = NS(devices=[selected,other,cpu], peer_memory=True,
                   get_devices_for_type=lambda backend:[selected,other,cpu])
        fake = NS(context=NS(preferences=NS(addons={'cycles':NS(preferences=prefs)})))
        with patch.dict(sys.modules, bpy=fake):
            actual = devices.configure(request())
            self.assertTrue(selected.use)
            self.assertFalse(other.use)
            self.assertFalse(cpu.use)
            self.assertEqual(actual['devices'], [{'name':'GPU A','type':'OPTIX','id':'gpu-a'}])
            with self.assertRaisesRegex(ValueError,'unavailable'):
                devices.configure(request() | {'devices':[{'name':'Removed GPU','type':'OPTIX','id':'absent'}]})
            self.assertFalse(cpu.use)

    def test_cpu_without_gpu_denoising_never_queries_unused_gpu(self):
        prefs = NS(peer_memory=True, get_device_list=lambda backend:[('CPU','CPU','CPU')])
        fake = NS(context=NS(preferences=NS(addons={'cycles':NS(preferences=prefs)})))
        value = {'engine':'CYCLES','mode':'CPU','backend':'CPU',
                 'preferences':request(),'preferences_required':False}
        with patch.dict(sys.modules, bpy=fake):
            actual = devices.configure(value)
            self.assertEqual(actual['backend'], 'CPU')
            self.assertEqual(actual['devices'][0]['type'], 'CPU')
            self.assertTrue(actual['preferences']['not_required'])
            self.assertEqual(prefs.compute_device_type, 'NONE')

    def test_frozen_scene_must_match_its_request(self):
        scene = NS(render=NS(engine='CYCLES',use_persistent_data=False),
                   cycles=NS(samples=8,device='GPU',use_adaptive_sampling=True,use_denoising=False))
        value = {'schema':1,'engine':'CYCLES','samples':8,'device':'GPU',
                 'adaptive_sampling':True,'denoising':False,'persistent_data':False,'request':request()}
        worker.verify_saved_settings(scene, value)
        for field, invalid in [('schema',True),('samples',True),('samples',16),('denoising',True),
                               ('request',{'engine':'CYCLES','mode':'CPU','backend':'CPU'})]:
            with self.assertRaises(ValueError): worker.verify_saved_settings(scene, value | {field:invalid})


if __name__ == '__main__':
    unittest.main()
