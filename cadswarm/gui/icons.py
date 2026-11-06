"""CADSwarm's own command icons: original thin-stroke line drawings on a 24 px grid, made in code.

Each icon is a few SVG elements drawn with a 1.5 px stroke in the theme's icon colour; parts drawn in
``@A`` take the accent (the new feature, the direction of an extrude, the selected face...). ``svg(name)``
returns the full SVG text (pure, testable); ``qicon(name, ...)`` renders it for Qt.
"""

ICONS = {
    # ------------------------------------------------------------------ quick access
    "new": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path stroke="@A" d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7h6l2 2h10v10H3z"/><path stroke="@A" d="M12 17v-5m-2.5 2.5L12 12l2.5 2.5"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><rect stroke="@A" x="8" y="13" width="8" height="5"/>',
    "undo": '<path d="M9 7 5 11l4 4"/><path d="M5 11h9a5 5 0 0 1 0 10h-3"/>',
    "redo": '<path d="m15 7 4 4-4 4"/><path d="M19 11h-9a5 5 0 0 0 0 10h3"/>',
    "rebuild": '<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path stroke="@A" d="M19 4v4h-4"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
    # ------------------------------------------------------------------ features
    "sketch": '<path d="M3 17 9 21l12-6-6-4z"/><path stroke="@A" d="M8 15.5 11 13l4 1.6"/>',
    "body": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
    "extrude": '<path d="M4 18 9 21l11-5-5-3z"/><path stroke="@A" d="M12 15V4m-3 3 3-3 3 3"/>',
    "revolve": '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M12 6c4 0 6 2 6 6s-2 6-6 6"/>'
               '<path stroke="@A" d="M12 18c-4 0-6-2-6-6 0-2 .6-3.5 1.7-4.5"/><path stroke="@A" d="m5.5 6 2.2 1.5L6.4 10"/>',
    "loft": '<ellipse cx="12" cy="18" rx="7" ry="2.5"/><rect x="8" y="3.5" width="8" height="4" rx="1"/>'
            '<path stroke="@A" d="M5 18 8 7.5M19 18 16 7.5"/>',
    "sweep": '<path stroke="@A" d="M4 20c4 0 4-8 8-8s4-8 8-8"/><circle cx="4" cy="20" r="2"/><circle cx="20" cy="4" r="2"/>',
    "cut": '<path d="M3 15 12 19l9-4-9-4z"/><path stroke="@A" d="M9 13.5 12 15l3-1.5-3-1.5zM12 3v8m-3-3 3 3 3-3"/>',
    "hole": '<path d="M3 15 12 19l9-4-9-4z"/><ellipse stroke="@A" cx="12" cy="15" rx="3" ry="1.4"/>'
            '<path stroke="@A" d="M9 15V6M15 15V6"/><ellipse cx="12" cy="6" rx="3" ry="1.4"/>',
    "groove": '<path d="M5 20V6a7 7 0 0 1 14 0v14"/><path stroke="@A" d="M9 20v-6a3 3 0 0 1 6 0v6"/>',
    "fillet": '<path d="M4 20V4"/><path d="M4 20h16"/><path stroke="@A" d="M4 10a10 10 0 0 0 10 10"/>',
    "chamfer": '<path d="M4 20V4M4 20h16"/><path stroke="@A" d="M4 11l9 9"/>',
    "shell": '<path d="M4 8 12 4l8 4v8l-8 4-8-4z"/><path stroke="@A" d="M7 9.5 12 7l5 2.5v5L12 17l-5-2.5z"/>',
    "draft": '<path d="M3 20h18"/><path d="M7 20 9 5h6l2 15"/><path stroke="@A" d="M9 5v15" stroke-dasharray="2 2"/>',
    "linear_pattern": '<rect x="3" y="9" width="5" height="6"/><rect stroke="@A" x="10" y="9" width="5" height="6"/>'
                      '<rect stroke="@A" x="17" y="9" width="4" height="6"/><path d="M3 19h18m-3-2 3 2-3 2"/>',
    "polar_pattern": '<circle cx="12" cy="12" r="7" stroke-dasharray="2 2.5"/><circle cx="12" cy="5" r="2"/>'
                     '<circle stroke="@A" cx="19" cy="12" r="2"/><circle stroke="@A" cx="12" cy="19" r="2"/>'
                     '<circle stroke="@A" cx="5" cy="12" r="2"/>',
    "mirror": '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 7 4 17h5z"/><path stroke="@A" d="m15 7 5 10h-5z"/>',
    "boolean": '<circle cx="9" cy="12" r="6"/><circle stroke="@A" cx="15" cy="12" r="6"/>',
    "fuse": '<path d="M9 6a6 6 0 1 0 3 11.2A6 6 0 1 0 12 6.8 6 6 0 0 0 9 6z"/>',
    "common": '<circle cx="9" cy="12" r="6" stroke-dasharray="2 2"/><circle cx="15" cy="12" r="6" stroke-dasharray="2 2"/>'
              '<path stroke="@A" d="M12 6.8a6 6 0 0 1 0 10.4 6 6 0 0 1 0-10.4z"/>',
    "plane": '<path d="M3 16 9 8h12l-6 8z"/><path stroke="@A" d="M12 12V3"/>',
    "axis": '<path stroke="@A" d="M5 19 19 5"/><path d="M15 5h4v4"/><circle cx="5" cy="19" r="1.6"/>',
    "point": '<path d="M12 4v4M12 16v4M4 12h4M16 12h4"/><circle stroke="@A" cx="12" cy="12" r="2.5"/>',
    # ------------------------------------------------------------------ sketch
    "line": '<path stroke="@A" d="M5 19 19 5"/><circle cx="5" cy="19" r="1.6"/><circle cx="19" cy="5" r="1.6"/>',
    "polyline": '<path stroke="@A" d="M4 18 9 7l6 9 5-11"/><circle cx="4" cy="18" r="1.4"/><circle cx="20" cy="5" r="1.4"/>',
    "rectangle": '<rect stroke="@A" x="4" y="6" width="16" height="12"/><circle cx="4" cy="6" r="1.4"/>'
                 '<circle cx="20" cy="18" r="1.4"/>',
    "circle": '<circle stroke="@A" cx="12" cy="12" r="7.5"/><path d="M12 12h7.5"/><circle cx="12" cy="12" r="1.2"/>',
    "arc": '<path stroke="@A" d="M5 17a8 8 0 0 1 14 0"/><circle cx="5" cy="17" r="1.4"/><circle cx="19" cy="17" r="1.4"/>'
           '<circle cx="12" cy="17" r="1"/>',
    "slot": '<rect stroke="@A" x="3" y="8" width="18" height="8" rx="4"/><circle cx="7" cy="12" r="1"/>'
            '<circle cx="17" cy="12" r="1"/>',
    "polygon": '<path stroke="@A" d="m12 3 8 5v8l-8 5-8-5V8z"/>',
    "trim": '<path d="M4 12h16"/><path stroke="@A" d="M12 4v16"/><circle cx="7" cy="6" r="2"/><circle cx="7" cy="18" r="2"/>'
            '<path d="m8.5 7.5 6 6M8.5 16.5l6-6"/>',
    "offset": '<path d="M4 18V9a5 5 0 0 1 5-5h11"/><path stroke="@A" d="M8 20v-9a3 3 0 0 1 3-3h9"/>',
    "external": '<path d="M3 17 9 21l12-6-6-4z"/><path stroke="@A" d="M15 11V3m-3 3 3-3 3 3"/>',
    "construction": '<path stroke="@A" d="M4 20 20 4" stroke-dasharray="3 2.5"/><circle cx="12" cy="12" r="6"/>',
    "dimension": '<path d="M4 6v12M20 6v12"/><path stroke="@A" d="M4 12h16M7 9.5 4 12l3 2.5M17 9.5l3 2.5-3 2.5"/>',
    "coincident": '<path d="M4 18 12 12M20 18l-8-6"/><circle stroke="@A" cx="12" cy="12" r="2.5"/>',
    "horizontal": '<path stroke="@A" d="M4 12h16"/><path d="M7 6h10M7 18h10"/>',
    "vertical": '<path stroke="@A" d="M12 4v16"/><path d="M6 7v10M18 7v10"/>',
    "finish": '<path d="M3 17 9 21l12-6-6-4z"/><path stroke="@A" d="m8 8 3 3 6-7"/>',
    # ------------------------------------------------------------------ evaluate
    "measure": '<path d="M3 16 16 3l5 5L8 21z"/><path d="m7 12 2 2M10 9l2 2M13 6l2 2"/>',
    "mass": '<path d="M6 9h12l2 11H4z"/><circle stroke="@A" cx="12" cy="6" r="2.5"/>',
    "interference": '<rect x="3" y="7" width="11" height="10"/><rect x="10" y="4" width="11" height="10"/>'
                    '<path stroke="@A" d="M10 7h4v7h-4z"/>',
    "section": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" d="M2 12h20" stroke-dasharray="3 2"/>',
    "printability": '<path d="M5 20h14"/><path d="M8 20 9 9h6l1 11"/><path stroke="@A" d="M10 5h4M12 5v4"/>',
    "simulate": '<path d="M4 20h16"/><path d="M6 20V10h12v10"/><path stroke="@A" d="M9 4v4m-2-2 2 2 2-2M15 4v4m-2-2 2 2 2-2"/>',
    "lighten": '<rect x="4" y="4" width="16" height="16"/><path stroke="@A" d="M4 12 12 4l8 8-8 8z"/>'
               '<path stroke="@A" d="m8 8 8 8M16 8l-8 8"/>',
    "check": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" d="m8 12 3 3 5-6"/>',
    "geometry": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><circle stroke="@A" cx="17" cy="17" r="3.5"/>'
                '<path stroke="@A" d="m19.5 19.5 2 2"/>',
    # ------------------------------------------------------------------ assembly
    "assembly": '<rect x="3" y="12" width="9" height="8"/><rect x="12" y="4" width="9" height="8"/>'
                '<path stroke="@A" d="M12 12v8h9v-8z"/>',
    "insert": '<rect x="4" y="9" width="12" height="11"/><path stroke="@A" d="M15 3h6v6M21 3l-8 8"/>',
    "fixed": '<path d="M4 20h16"/><path d="M6 20l2-3M10 20l2-3M14 20l2-3"/><rect stroke="@A" x="7" y="5" width="10" height="10"/>',
    "revolute": '<circle cx="12" cy="12" r="3"/><path stroke="@A" d="M19 12a7 7 0 1 1-2-4.9"/><path stroke="@A" d="M19 4v4h-4"/>',
    "slider": '<path d="M3 16h18"/><rect stroke="@A" x="8" y="10" width="8" height="6"/><path d="m5 8-2 2 2 2M19 8l2 2-2 2"/>',
    "solve": '<path d="M5 12h3l2-5 4 10 2-5h3"/>',
    "move": '<path d="M12 3v18M3 12h18"/><path stroke="@A" d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    "constrain": '<path d="M9 15 15 9"/><path d="M7 11 5 13a4 4 0 0 0 6 6l2-2"/><path stroke="@A" d="m17 13 2-2a4 4 0 0 0-6-6l-2 2"/>',
    "explode": '<rect x="9" y="9" width="6" height="6"/><path stroke="@A" d="M5 5l3 3M19 5l-3 3M5 19l3-3M19 19l-3-3"/>',
    # ------------------------------------------------------------------ library
    "library": '<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path stroke="@A" d="m16 5 3.6-1 3 15.4-3.6 1z"/>',
    "recommend": '<path stroke="@A" d="m12 3 2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z"/>',
    "screw": '<path d="M8 3h8v4H8z"/><path d="M10 7v13l2 1.5 2-1.5V7"/><path stroke="@A" d="m10 10 4 1.5M10 13l4 1.5M10 16l4 1.5"/>',
    "bearing": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5"/>'
               '<circle stroke="@A" cx="12" cy="6" r="1.3"/><circle stroke="@A" cx="18" cy="12" r="1.3"/>'
               '<circle stroke="@A" cx="12" cy="18" r="1.3"/><circle stroke="@A" cx="6" cy="12" r="1.3"/>',
    "insert_nut": '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><circle stroke="@A" cx="12" cy="12" r="3.5"/>',
    "magnet": '<path d="M6 4v8a6 6 0 0 0 12 0V4"/><path d="M10 4v8a2 2 0 0 0 4 0V4"/><path stroke="@A" d="M6 7h4M14 7h4"/>',
    "board": '<rect x="3" y="5" width="18" height="14" rx="1"/><rect stroke="@A" x="9" y="9" width="6" height="6"/>'
             '<path d="M12 5v4M12 15v4M3 12h6M15 12h6"/>',
    "motor": '<rect x="3" y="7" width="13" height="10" rx="1"/><path stroke="@A" d="M16 12h5"/><path d="M6 7v10M13 7v10"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1'
                'M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "style": '<path d="M4 20c0-3 2-5 5-5"/><path stroke="@A" d="m9 15 10-10 2 2-10 10z"/>',
    # ------------------------------------------------------------------ AI build
    "plan": '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 3v2h6V3"/>'
            '<path stroke="@A" d="M8 10h1M11 10h5M8 14h1M11 14h5M8 18h1M11 18h3"/>',
    "run_next": '<path stroke="@A" d="M7 5v14l10-7z"/>',
    "run_all": '<path d="M4 5v14l8-7z"/><path stroke="@A" d="M12 5v14l8-7z"/>',
    "pause": '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>',
    "replan": '<rect x="5" y="3" width="14" height="18" rx="1"/><path stroke="@A" d="M15 13a3.5 3.5 0 1 1-1-2.5M15 8.5V11h-2.5"/>',
    "assign": '<circle cx="9" cy="8" r="3.5"/><path d="M3 20a6 6 0 0 1 12 0"/><path stroke="@A" d="M16 12h6m-3-3 3 3-3 3"/>',
    "review": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle stroke="@A" cx="12" cy="12" r="3"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/><path stroke="@A" d="M8 9h8M8 12h5"/>',
    "tasks": '<path d="M9 6h11M9 12h11M9 18h11"/><path stroke="@A" d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11"/>'
             '<path d="M3.5 17.5h3v1.5h-3z"/>',
    "helper": '<rect x="5" y="8" width="14" height="11" rx="2"/><path d="M12 4v4"/><circle cx="12" cy="3.5" r="1"/>'
              '<circle stroke="@A" cx="9" cy="13" r="1.3"/><circle stroke="@A" cx="15" cy="13" r="1.3"/>',
    "invite": '<circle cx="9" cy="8" r="3.5"/><path d="M3 20a6 6 0 0 1 12 0"/><path stroke="@A" d="M19 8v6M16 11h6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="1"/><path stroke="@A" d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "release": '<rect x="5" y="11" width="14" height="10" rx="1"/><path stroke="@A" d="M8 11V8a4 4 0 0 1 7.7-1.5"/>',
    "edit_part": '<path d="M12 3 20 7.5v5"/><path d="M4 7.5v9L12 21l2-1.1M4 7.5 12 3"/>'
                 '<path stroke="@A" d="m14 20 1-4 6-6 3 3-6 6z"/>',
    "export": '<path d="M5 13v7h14v-7"/><path stroke="@A" d="M12 15V3m-4 4 4-4 4 4"/>',
    "pointers": '<path stroke="@A" d="M5 3v15l4-4 3 7 2.5-1-3-7h5.5z"/>',
    "connect": '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/>'
               '<path stroke="@A" d="m8.3 10.9 7.4-3.8M8.3 13.1l7.4 3.8"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="1"/><path stroke="@A" d="M14 4v16"/>',
    # ------------------------------------------------------------------ view
    "fit": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect stroke="@A" x="8" y="8" width="8" height="8"/>',
    "iso": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" d="M4 7.5 12 12l8-4.5M12 12v9"/>',
    "front": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" fill="@AF" d="M4 7.5 12 12v9l-8-4.5z"/>',
    "top": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" fill="@AF" d="M4 7.5 12 3l8 4.5-8 4.5z"/>',
    "right": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path stroke="@A" fill="@AF" d="M20 7.5 12 12v9l8-4.5z"/>',
    "rear": '<rect x="4" y="4" width="16" height="16" stroke-dasharray="2 2"/><path stroke="@A" d="M9 9h6v6H9z"/>',
    "bottom": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z" stroke-dasharray="2 2"/><path stroke="@A" d="M4 16.5 12 12l8 4.5-8 4.5z"/>',
    "left": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z" stroke-dasharray="2 2"/><path stroke="@A" d="M4 7.5 12 3v9l-8 4.5z"/>',
    "shaded_edges": '<path fill="@AF" d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
    "shaded": '<path stroke="@A" fill="@AF" d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/>',
    "wireframe": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>'
                 '<path stroke="@A" d="M4 16.5 12 12l8 4.5M12 3v9" stroke-dasharray="2 2"/>',
    "hidden_lines": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
    "visibility": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'
                  '<path stroke="@A" d="M4 20 20 4"/>',
    "show": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle stroke="@A" cx="12" cy="12" r="3"/>',
    "perspective": '<path d="M3 6 21 3v18L3 18z"/><path stroke="@A" d="M3 12h18"/>',
    "ortho": '<rect x="4" y="4" width="16" height="16"/><path stroke="@A" d="M4 12h16M12 4v16"/>',
    "theme": '<circle cx="12" cy="12" r="8"/><path fill="@AF" stroke="@A" d="M12 4a8 8 0 0 1 0 16z"/>',
    "classic": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18"/><path stroke="@A" d="m9 13 3 3 3-3"/>',
}

STROKE = 1.5


def svg(name, colour="#353b44", accent="#2b6cd9", size=24):
    """The SVG text of one icon. Unknown names get a plain rounded square (never an exception)."""
    body = ICONS.get(name) or '<rect x="5" y="5" width="14" height="14" rx="2"/>'
    body = body.replace('"@AF"', f'"{accent}" fill-opacity="0.18"').replace('"@A"', f'"{accent}"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="{colour}" stroke-width="{STROKE}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>')


_CACHE = {}


def qicon(name, colour="#353b44", accent="#2b6cd9", disabled="#a0a7b0", sizes=(16, 24, 32)):
    """A QIcon with crisp pixmaps for each size (normal and disabled). Returns an empty QIcon on any failure."""
    from PySide import QtCore, QtGui
    key = (name, colour, accent, disabled, tuple(sizes))
    if key in _CACHE:
        return _CACHE[key]
    icon = QtGui.QIcon()
    try:
        for size in sizes:
            for mode, (c, a) in ((QtGui.QIcon.Normal, (colour, accent)), (QtGui.QIcon.Disabled, (disabled, disabled))):
                pm = _render(svg(name, c, a, size), size, QtCore, QtGui)
                if pm is not None:
                    icon.addPixmap(pm, mode)
    except Exception:  # noqa: BLE001 - an icon must never break a toolbar
        icon = QtGui.QIcon()
    _CACHE[key] = icon
    return icon


def _render(text, size, QtCore, QtGui):
    data = QtCore.QByteArray(text.encode("utf-8"))
    ratio = 2  # draw at twice the size: sharp on high-DPI screens, Qt scales down on others
    try:
        from PySide import QtSvg
        renderer = QtSvg.QSvgRenderer(data)
        img = QtGui.QImage(size * ratio, size * ratio, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        renderer.render(p)
        p.end()
        pm = QtGui.QPixmap.fromImage(img)
        pm.setDevicePixelRatio(ratio)
        return pm
    except ImportError:
        pm = QtGui.QPixmap()
        return pm if pm.loadFromData(data, "SVG") else None
