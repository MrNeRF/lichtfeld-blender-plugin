# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixel-measured paragraphs; wrapping adapts to sidebar width and UI scale."""

def draw_disclosure(layout, owner, name, title, *, nested=True):
    """Expand within the existing surface without an overlapping panel background."""
    if nested:
        row = layout.split(factor=.04, align=True)
        spacer = row.column()
        spacer.label(text='')
        layout = row.column()
    opened = getattr(owner, name)
    header = layout.row(align=True)
    header.alignment = 'LEFT'
    header.use_property_split = False
    header.use_property_decorate = False
    header.prop(owner, name, text=title, emboss=False,
                icon='TRIA_DOWN' if opened else 'TRIA_RIGHT')
    return layout.column() if opened else None


def wrap_pixels(text, width, measure):
    width = max(1, width)
    lines = []
    for paragraph in str(text).splitlines():
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if line and measure(candidate) > width:
                lines.append(line); line = ""
            while measure(word) > width and len(word) > 1:
                cut = 1
                while cut < len(word) and measure(word[:cut + 1]) <= width:
                    cut += 1
                lines.append(word[:cut]); word = word[cut:]
            line = (line + " " + word).strip()
        if line:
            lines.append(line)
    return lines


def draw_paragraph(container, text, region_width, ui_scale=1.0):
    import blf
    # Leave headroom for Blender's label font metrics and nested box padding.
    blf.size(0, 13 * ui_scale)
    measure = lambda value: blf.dimensions(0, value)[0]
    column = container.column(align=True)
    column.scale_y = .85
    for line in wrap_pixels(text, max(80, region_width - 56 * ui_scale), measure):
        column.label(text=line)
