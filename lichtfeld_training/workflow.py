# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure workflow rules shared by controls and tests."""
PRESETS = {
 'DRAFT': (2000,500,48,512,16,250000),
 'BALANCED': (7000,1000,96,768,32,500000),
 'HIGH': (30000,2000,144,1024,64,1000000),
}
PRESET_FIELDS=('iterations','eval_interval','views','resolution','samples','max_points')
CAPTURE_PHASES={'preparing','preparing capture','preparing source snapshot','rendering','exporting dataset','finalizing capture'}
STARTUP_PHASES={'validating backend','starting','preparing numerical runtime'}
TRAINING_PHASES={'training','paused'}
TERMINAL={'completed','stopped','failed'}

def phase(value): return str(value or '').replace('_',' ').lower()

def status_label(value):
 state=phase(value)
 return {'controller lost':'Job interrupted','finalizing capture':'Preparing dataset',
         'connecting':'Connecting automatically','finalizing':'Saving training result'}.get(state,state.capitalize())

def preset_name(values):
 for key,expected in PRESETS.items():
  if tuple(values[name] for name in PRESET_FIELDS)==expected:return key
 return 'CUSTOM'

def actions(state, external=False, result=False, best=False, checkpoint=False, busy=False, control=True):
 state=phase(state)
 return {
  'pause':not external and control and state in TRAINING_PHASES,
  'stop':not external and control and state in CAPTURE_PHASES|STARTUP_PHASES|TRAINING_PHASES,
  'previews':not external and control and state in TRAINING_PHASES,
  'import':not busy and not external and best,
  'compare':not busy and result,
  'continue':not external and not busy and checkpoint and state in TERMINAL,
 }


# Only these settings can change when training an already captured dataset.
TRAINING_FIELDS = (
 'iterations', 'eval_interval', 'max_points', 'sh_degree', 'strategy', 'mask_mode',
 'use_depth_loss', 'use_normal_loss', 'depth_loss_weight', 'normal_loss_weight',
 'normal_start_fraction', 'normal_end_fraction', 'preview_seconds', 'preview_points',
 'full_preview', 'preview_sh_degree', 'preview_mode', 'optimizer', 'optimizer_schedule', 'optimizer_file',
)
