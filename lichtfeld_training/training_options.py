# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible optimizer controls; all values are fixed for a started job."""
import json
from pathlib import Path

PROFILES = json.loads(Path(__file__).with_name('training_profiles.json').read_text(encoding='utf-8'))
GROUPS = {
    'Learning rates': ('means_lr', 'means_lr_end', 'shs_lr', 'opacity_lr',
                       'scaling_lr', 'scaling_lr_end', 'rotation_lr', 'sh_degree_interval'),
    'Refinement': ('refine_every', 'growth_grad_threshold', 'grow_fraction',
                   'use_error_map', 'use_edge_map', 'opacity_decay', 'scale_decay'),
    'Loss and regularization': ('lambda_dssim', 'opacity_reg', 'scale_reg',
                                'erank_reg', 'normal_consistency_weight', 'normal_flatten_weight'),
    'Appearance': ('use_exposure_correction', 'use_ppisp', 'use_bilateral_grid',
                   'mip_filter', 'enable_sparsity'),
}
FIELDS = tuple(name for group in GROUPS.values() for name in group)
PROBABILITIES = {'lambda_dssim','grow_fraction','opacity_decay','scale_decay'}
LABELS = {
    'means_lr': 'Position LR', 'means_lr_end': 'Final position LR',
    'shs_lr': 'Color LR (-1: auto)', 'opacity_lr': 'Opacity LR',
    'scaling_lr': 'Scale LR', 'scaling_lr_end': 'Final scale LR',
    'rotation_lr': 'Rotation LR', 'lambda_dssim': 'SSIM loss weight',
    'normal_consistency_weight':'Normal consistency', 'normal_flatten_weight':'Flatten weight',
    'growth_grad_threshold':'Growth gradient', 'refine_every':'Refine interval',
    'opacity_reg':'Opacity regularizer', 'scale_reg':'Scale regularizer', 'erank_reg':'Rank regularizer',
    'max_cap': 'Gaussian budget', 'grow_fraction': 'Growth fraction (-1: auto)',
}


def optimizer_values(settings):
    result = {name: getattr(settings, name) for name in FIELDS}
    result.update(start_refine=round(settings.iterations*settings.refine_start),
                  stop_refine=max(1, round(settings.iterations*settings.refine_end)),
                  grow_until_iter=max(1, round(settings.iterations*settings.growth_end)),
                  keep_all_checkpoints=settings.keep_all_checkpoints)
    return result


def validate(strategy, values):
    errors = []
    for name in FIELDS:
        if name not in values or isinstance(values[name],bool):
            continue
        value = values[name]
        if value < 0 and not (strategy=='mrnf' and name in {'shs_lr','grow_fraction'} and value==-1):
            errors.append(f'{name} must be nonnegative (-1 auto is MRNF only).')
        if name in PROBABILITIES and value>1:
            errors.append(f'{name} must not exceed 1.')
    if values.get('use_exposure_correction') and any(values.get(k) for k in
            ('use_ppisp','use_bilateral_grid','ppisp_use_controller','ppisp_freeze_from_sidecar')):
        errors.append('Exposure correction replaces standalone PPISP and the bilateral grid.')
    return errors
