# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Portal Gallery controls; network, files and decoding live on workers."""
import time
import queue
from pathlib import Path
import bpy
import bpy.utils.previews
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix
from . import capture_preview, preview_scene, ui_runtime, rig_edit
from .session_geometry import source_model_transform
from .portal_runtime import Runtime

_runtime = None
_icons = None
_generation = -1
_rows = None
_stage = None
_destination = None
_pending = None
_thumbnail = ""
_identity = None
_last_revision = -1
_last_online = None
_refresh_wanted = False
_frame = None
_import_cancel = None
_popup_region = None


def state():
    return _runtime.snapshot() if _runtime else {"phase": "idle", "rows": (), "message": "", "progress": 0}


def runtime():
    global _runtime
    if _runtime is None:
        directory = Path(bpy.utils.user_resource("CONFIG")) / "lichtfeld_portal"
        data = (Path(bpy.utils.extension_path_user(__package__, path="portal", create=False))
                if __package__.startswith("bl_ext.") else Path(bpy.utils.user_resource("DATAFILES")) / "lichtfeld_portal")
        _runtime = Runtime(directory, data)
        _runtime.start("initialize")
    return _runtime


def available(scene):
    if capture_preview.is_preview(scene) or preview_scene.is_preview(scene):
        return "Return to your source scene to import an asset."
    if rig_edit.render_busy(bpy.context):
        return "Wait for rendering to finish before importing."
    settings = scene.lichtfeld_training
    current = ui_runtime.peek(scene)
    if settings.operation_busy or settings.running and (not current or current.status.get("state") not in ("training", "paused")):
        return "Wait for scene preparation or capture to finish."
    return ""


def selected(context):
    settings = context.window_manager.lichtfeld_portal
    row = settings.rows[settings.index] if 0 <= settings.index < len(settings.rows) else None
    if row and settings.search:
        flags = bpy.types.UI_UL_list.filter_items_by_name(settings.search, 1, [row], "title")
        if not flags[0]:
            return None
    return row


def import_target(scene):
    if capture_preview.is_preview(scene):
        return capture_preview.source_scene(scene)
    if preview_scene.is_preview(scene):
        return preview_scene.source_scene(scene)
    return scene


def import_steps(scene, identifier, count, fields):
    cloud = yield from ui_runtime.pointcloud_steps(fields, count, "Portal splat")
    committed = False
    obj = None
    try:
        if not bpy.app.background and bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message="Before Portal import")
        title = next((row["title"] for row in state()["rows"] if row["id"] == identifier), "Portal splat")
        obj = bpy.data.objects.new(title, cloud)
        obj.matrix_world = Matrix(source_model_transform().tolist())
        obj["lichtfeld_portal_scene"] = identifier
        scene.collection.objects.link(obj)
        committed = True
        if bpy.context.scene == scene:
            for item in bpy.context.selected_objects:
                item.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
        if not bpy.app.background and bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message="Import Portal splat")
        runtime().update(phase="idle", message="Imported into " + scene.name)
    finally:
        if not committed:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if cloud.users == 0:
                bpy.data.pointclouds.remove(cloud)


def before_history():
    global _stage, _pending, _generation
    if _stage:
        _stage.close()
        _stage = None
        _pending = _frame
        if _runtime:
            _runtime.update(phase="ready", message="Waiting to add the asset after Undo")
    _generation = -1


def destination():
    return next((scene for scene in bpy.data.scenes if scene.session_uid == _destination), None)


def cancel_import():
    global _pending, _frame, _destination
    if _import_cancel:
        _import_cancel.set()
    _pending = None
    _frame = None
    if _stage is None:
        _destination = None


def invalidate():
    global _stage, _destination, _pending, _generation, _frame, _popup_region
    if _runtime and (_destination is not None or _stage is not None or _pending is not None):
        _runtime.stop(cancel_login=False)
        _runtime.update(phase="canceled", message="Import canceled because the scene changed")
    if _stage:
        _stage.close()
    _stage = None
    _destination = None
    _pending = None
    _frame = None
    _generation = -1
    _popup_region = None


def tick():
    try:
        return tick_steps()
    except Exception as error:
        import logging
        from lichtfeld_portal.gallery_logging import safe_text
        logging.getLogger(__name__).warning("Portal Gallery UI update failed: %s", safe_text(error))
        if _runtime:
            _runtime.update(phase="failed", message="The gallery view could not update. Reopen it to try again.")
        return 0.5


def tick_steps():
    global _stage, _pending, _rows, _generation, _thumbnail, _identity, _last_revision, _destination
    global _last_online, _refresh_wanted, _frame
    if _runtime is None:
        runtime()
        return 0.2
    online = bpy.app.online_access
    if not online and _last_online is not False:
        _runtime.stop()
        cancel_import()
    _last_online = online
    before = _runtime.snapshot()
    account = before.get("account")
    identity = (account.email, account.connected_since) if account and account.signed_in and account.email and account.connected_since else None
    if identity != _identity:
        if _identity is not None:
            invalidate()
            _runtime.update(rows=(), bump_generation=True)
            for key in tuple(_icons):
                del _icons[key]
        _identity = identity
        _refresh_wanted = bool(identity)
    if online and _refresh_wanted and before["phase"] in ("idle", "canceled", "failed"):
        if _runtime.start("refresh"):
            _refresh_wanted = False
    while True:
        try:
            kind, value = _runtime.events.get_nowait()
        except queue.Empty:
            break
        if kind == "browser":
            current = _runtime.snapshot().get("account")
            if online and current and current.linking and current.verification_uri_complete == value:
                bpy.ops.wm.url_open(url=value)
        elif kind == "callback":
            if online:
                value()
        elif kind == "import":
            expected_identity, token, *payload = value
            if expected_identity == identity and token is _import_cancel and not token.is_set() and _destination is not None:
                _pending = payload
        elif kind == "thumbnail":
            identifier, path = value
            try:
                for key in tuple(_icons):
                    del _icons[key]
                _icons.load(identifier, path, "IMAGE")
            except (OSError, RuntimeError):
                pass  # A missing thumbnail must not prevent importing the asset.
        elif kind == "cache_cleared":
            for key in tuple(_icons):
                del _icons[key]
    snapshot = _runtime.snapshot()
    if _import_cancel and _import_cancel.is_set():
        cancel_import()
    if snapshot["phase"] == "ready" and _runtime.cancel.is_set():
        _runtime.update(phase="canceled", message="Import canceled")
    if snapshot["generation"] != _generation:
        _generation = snapshot["generation"]
        bpy.context.window_manager.lichtfeld_portal.rows.clear()
        _rows = iter(snapshot["rows"])
        _thumbnail = ""
    deadline = time.monotonic() + 0.003
    rows_changed = False
    while _rows and time.monotonic() < deadline:
        row = next(_rows, None)
        if row is None:
            _rows = None
            break
        item = bpy.context.window_manager.lichtfeld_portal.rows.add()
        item.identifier, item.title, item.description = row["id"], row["title"], row["description"]
        rows_changed = True
    row = selected(bpy.context)
    if online and row and row.identifier != _thumbnail and snapshot["phase"] == "idle":
        if _runtime.start("thumbnail", row.identifier):
            _thumbnail = row.identifier
    if _pending and _stage is None:
        try:
            scene = destination()
            if scene is None:
                raise ValueError("The destination scene was closed")
            reason = available(scene)
            if not reason:
                _frame = _pending
                _stage = import_steps(scene, *_pending)
                _pending = None
                _runtime.update(phase="importing", message="Adding splats to the scene…")
            elif snapshot["message"] != reason:
                _runtime.update(message=reason)
        except (ReferenceError, ValueError) as error:
            _pending = None
            _destination = None
            _runtime.update(phase="failed", message=str(error))
    if _stage and not rig_edit.render_busy(bpy.context):
        try:
            scene = destination()
            if scene is None or _import_cancel and _import_cancel.is_set():
                _stage.close()
                _stage = None
                _destination = None
                _frame = None
                _runtime.update(phase="canceled", message="Import canceled")
            elif not available(scene):
                next(_stage)
        except StopIteration:
            _stage = None
            _destination = None
            _frame = None
        except Exception as error:
            _stage = None
            _destination = None
            _frame = None
            _runtime.update(phase="failed", message=str(error))
    if snapshot["revision"] != _last_revision or rows_changed:
        _last_revision = snapshot["revision"]
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("VIEW_3D", "PREFERENCES"):
                    area.tag_redraw()
        global _popup_region
        if _popup_region is not None:
            try:
                _popup_region.tag_redraw()
            except ReferenceError:
                _popup_region = None
    return 0.1


class LF_PortalRow(bpy.types.PropertyGroup):
    identifier: StringProperty(options={"SKIP_SAVE"})
    title: StringProperty(options={"SKIP_SAVE"})
    description: StringProperty(options={"SKIP_SAVE"})


class LF_PortalState(bpy.types.PropertyGroup):
    rows: CollectionProperty(type=LF_PortalRow, options={"SKIP_SAVE"})
    index: IntProperty(name="Selected asset", description="Asset to preview and import", options={"SKIP_SAVE"})
    search: StringProperty(name="Search assets", description="Filter the Portal gallery by title", options={"SKIP_SAVE"})


class LF_UL_portal(bpy.types.UIList):
    def draw_filter(self, context, layout):
        layout.prop(self, "use_filter_sort_alpha", text="Sort by name")

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        layout.label(text=item.title, icon="OUTLINER_OB_POINTCLOUD")

    def filter_items(self, context, data, propname):
        rows = getattr(data, propname)
        search = context.window_manager.lichtfeld_portal.search
        flags = bpy.types.UI_UL_list.filter_items_by_name(search, self.bitflag_filter_item, rows, "title") if search else []
        order = bpy.types.UI_UL_list.sort_items_by_name(rows, "title") if self.use_filter_sort_alpha else []
        return flags, order


def account_controls(layout):
    snapshot = state()
    account = snapshot.get("account")
    if not bpy.app.online_access:
        layout.label(text="Blender online access is disabled", icon="INFO")
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_action", text="Open network preferences", icon="PREFERENCES").action = "network_preferences"
        return
    if account and account.signed_in:
        layout.label(text="Connected as " + account.display_name, icon="USER")
        row = layout.row()
        row.enabled = snapshot["phase"] in ("idle", "failed", "canceled", "thumbnail")
        row.operator("lichtfeld.portal_action", text="Disconnect Blender").action = "disconnect"
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_action", text="Sign out", icon="USER").action = "sign_out"
    elif account and account.linking:
        layout.label(text="Approve the connection in your browser", icon="URL")
        layout.label(text="Code: " + account.user_code)
        row = layout.row(align=True)
        row.operator("lichtfeld.portal_action", text="Open browser", icon="URL").action = "browser"
        row.operator("lichtfeld.portal_action", text="Copy code", icon="COPYDOWN").action = "copy"
        layout.operator("lichtfeld.portal_action", text="Cancel connection").action = "cancel"
    else:
        layout.operator("lichtfeld.portal_action", text="Connect account", icon="USER").action = "connect"
    if account and account.error:
        layout.label(text=account.error.replace("_", " "), icon="ERROR")
    if account and account.membership_required:
        layout.label(text="This account needs access to LichtFeld Portal", icon="INFO")


def gallery_controls(layout, context):
    layout.label(text="Portal Gallery", icon="ASSET_MANAGER")
    account_controls(layout)
    if not bpy.app.online_access:
        if state()["message"]:
            from .ui_text import draw_paragraph
            draw_paragraph(layout, state()["message"], 600, context.preferences.system.ui_scale)
        return
    snapshot = state()
    busy = snapshot["phase"] not in ("idle", "failed", "canceled", "thumbnail")
    row = layout.row()
    account = snapshot.get("account")
    row.enabled = bpy.app.online_access and not busy and bool(account and account.signed_in)
    row.operator("lichtfeld.portal_action", text="Refresh gallery", icon="FILE_REFRESH").action = "refresh"
    layout.prop(context.window_manager.lichtfeld_portal, "search", text="", icon="VIEWZOOM")
    layout.template_list("LF_UL_portal", "", context.window_manager.lichtfeld_portal, "rows",
                         context.window_manager.lichtfeld_portal, "index", rows=7)
    item = selected(context)
    if item:
        icon = _icons[item.identifier].icon_id if _icons and item.identifier in _icons else 0
        layout.template_icon(icon_value=icon, scale=8)
        layout.label(text=item.title)
        layout.label(text="Import: visible project splats · PLY")
        layout.label(text="Requires LichtFeld Studio and a supported CUDA GPU", icon="INFO")
        if item.description:
            from .ui_text import draw_paragraph
            description = item.description[:600] + ("…" if len(item.description) > 600 else "")
            draw_paragraph(layout, description, 600, context.preferences.system.ui_scale)
    target = import_target(context.scene)
    reason = available(target) if target else "The source scene is no longer available."
    if target:
        layout.label(text="Destination: " + target.name)
    if reason:
        layout.label(text=reason, icon="INFO")
    row = layout.row()
    row.enabled = bool(item) and target is not None and not busy and bpy.app.online_access
    row.operator("lichtfeld.portal_action", text="Download and queue import" if reason and target else "Import into scene", icon="IMPORT").action = "import"
    if context.active_object and context.active_object.get("lichtfeld_portal_scene"):
        layout.operator("lichtfeld.portal_frame", icon="VIEWZOOM")
    if busy:
        layout.label(text=snapshot["phase"].capitalize())
        layout.progress(factor=snapshot["progress"], type="BAR", text="Downloading" if snapshot["phase"] == "import" else snapshot["phase"].capitalize())
        if snapshot["phase"] not in ("disconnect", "sign_out"):
            layout.operator("lichtfeld.portal_action", text="Cancel", icon="CANCEL").action = "cancel"
    if snapshot["message"]:
        from .ui_text import draw_paragraph
        draw_paragraph(layout, snapshot["message"], 600, context.preferences.system.ui_scale)
    if snapshot.get("total_bytes"):
        layout.label(text=f"Download size: {snapshot['total_bytes'] / (1024 * 1024):.1f} MiB")
    if snapshot.get("details"):
        layout.operator("lichtfeld.portal_action", text="Copy error details", icon="COPYDOWN").action = "copy_error"


class LF_OT_portal_action(bpy.types.Operator):
    bl_idname = "lichtfeld.portal_action"
    bl_label = "Portal Gallery action"
    bl_description = "Connect, browse or import from your LichtFeld Portal gallery without changing the training job"
    action: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return {
            "import": "Download this asset and add its visible splats to the current scene; existing objects and training results stay intact",
            "refresh": "Check your Portal gallery for available assets",
            "connect": "Approve Blender's Portal connection in your browser; no password is entered in Blender",
            "disconnect": "Disconnect Blender's Portal account without changing LichtFeld Studio's connection",
            "cancel": "Cancel the pending connection or asset import; this does not stop training",
            "browser": "Open the Portal approval page in your browser",
            "copy": "Copy the device approval code to the clipboard",
            "sign_out": "Revoke Blender's authorization and forget the saved account so you can connect a different one",
            "clear_cache": "Remove cached Portal downloads and thumbnails; imported objects, credentials and training jobs remain",
            "network_preferences": "Open Blender's network preferences; online access is never enabled automatically",
            "copy_error": "Copy redacted backend diagnostics to help investigate the import failure",
        }.get(properties.action, cls.bl_description)

    def invoke(self, context, event):
        if self.action in ("sign_out", "clear_cache"):
            title = "Sign out of Portal?" if self.action == "sign_out" else "Clear Portal cache?"
            message = ("Forget Blender's authorization. LichtFeld Studio stays connected." if self.action == "sign_out"
                       else "Remove cached downloads and thumbnails. Imported objects and training jobs remain.")
            return context.window_manager.invoke_confirm(self, event, title=title, message=message)
        return self.execute(context)

    def execute(self, context):
        global _destination, _import_cancel
        if self.action == "cancel":
            cancel_import()
            runtime().stop()
            return {"FINISHED"}
        if self.action == "network_preferences":
            context.preferences.active_section = "SYSTEM"
            bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
            return {"FINISHED"}
        if self.action == "clear_cache":
            if not runtime().start("clear_cache"):
                self.report({"INFO"}, "Finish or cancel the active Portal operation before clearing its cache")
                return {"CANCELLED"}
            return {"FINISHED"}
        if self.action == "copy_error":
            context.window_manager.clipboard = state().get("details", "")
            return {"FINISHED"}
        if not bpy.app.online_access:
            self.report({"WARNING"}, "Enable online access in Blender Preferences to use Portal Gallery")
            return {"CANCELLED"}
        if self.action in ("browser", "copy"):
            account = state().get("account")
            if not account or not account.linking:
                return {"CANCELLED"}
            if self.action == "copy":
                context.window_manager.clipboard = account.user_code
            else:
                bpy.ops.wm.url_open(url=account.verification_uri_complete)
            return {"FINISHED"}
        if self.action == "import":
            item = selected(context)
            target = import_target(context.scene)
            if not item or target is None or _stage is not None:
                self.report({"WARNING"}, "Select an asset and an available source scene; finish any pending import first")
                return {"CANCELLED"}
            preferences = context.preferences.addons[__package__].preferences
            if runtime().start("import", item.identifier, bpy.path.abspath(preferences.backend_path)):
                _destination = target.session_uid
                _import_cancel = runtime().cancel
            else:
                self.report({"INFO"}, "The previous operation is finishing; try again shortly")
        else:
            runtime().start(self.action)
        return {"FINISHED"}


class LF_OT_portal_browser(bpy.types.Operator):
    bl_idname = "lichtfeld.portal_gallery"
    bl_label = "Portal Gallery"
    bl_description = "Browse your LichtFeld Portal assets and add splats to the current scene"

    def invoke(self, context, event):
        runtime()
        return context.window_manager.invoke_popup(self, width=650)

    def draw(self, context):
        global _popup_region
        _popup_region = context.region
        gallery_controls(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}

    def cancel(self, context):
        global _popup_region
        _popup_region = None


class LF_OT_portal_frame(bpy.types.Operator):
    bl_idname = "lichtfeld.portal_frame"
    bl_label = "Frame imported asset"
    bl_description = "Center a 3D viewport on the selected Portal splat"

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.get("lichtfeld_portal_scene"))

    def execute(self, context):
        area = context.area if context.area and context.area.type == "VIEW_3D" else next(
            (area for area in context.window.screen.areas if area.type == "VIEW_3D"), None)
        if area is None:
            self.report({"WARNING"}, "Open a 3D viewport to frame this asset")
            return {"CANCELLED"}
        region = next(region for region in area.regions if region.type == "WINDOW")
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_selected(use_all_regions=False)
        return {"FINISHED"}


class LF_PT_portal(bpy.types.Panel):
    bl_idname = "LF_PT_portal"
    bl_label = "Portal Gallery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LichtFeld"
    bl_order = -1

    def draw(self, context):
        row = self.layout.row(align=True)
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_gallery", text="Browse Portal Gallery…", icon="ASSET_MANAGER")
        row.operator("lichtfeld.setup", text="", icon="PREFERENCES")
        snapshot = state()
        account = snapshot.get("account")
        if not bpy.app.online_access:
            self.layout.label(text="Blender is offline", icon="INFO")
        elif account and account.signed_in:
            self.layout.label(text=account.display_name, icon="USER")
        elif account and account.linking:
            self.layout.label(text="Waiting for browser approval", icon="URL")
        if snapshot["phase"] not in ("idle", "failed", "canceled", "thumbnail"):
            self.layout.label(text=snapshot["message"] or snapshot["phase"].capitalize())
        elif snapshot["message"]:
            self.layout.label(text=snapshot["message"], icon="INFO")
        if context.active_object and context.active_object.get("lichtfeld_portal_scene"):
            self.layout.operator("lichtfeld.portal_frame", icon="VIEWZOOM")


CLASSES = (LF_PortalRow, LF_PortalState, LF_UL_portal, LF_OT_portal_action, LF_OT_portal_browser, LF_OT_portal_frame, LF_PT_portal)


def import_menu(self, context):
    row = self.layout.row()
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("lichtfeld.portal_gallery", text="LichtFeld Portal Gallery…")


def register():
    global _icons
    _icons = bpy.utils.previews.new()
    bpy.types.WindowManager.lichtfeld_portal = PointerProperty(type=LF_PortalState, options={"SKIP_SAVE"})
    bpy.types.TOPBAR_MT_file_import.append(import_menu)
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.2, persistent=True)


def unregister():
    global _runtime, _icons, _generation, _rows, _thumbnail, _identity, _last_revision
    global _last_online, _refresh_wanted, _import_cancel
    global _popup_region
    invalidate()
    if _runtime:
        _runtime.close()
        _runtime = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    bpy.types.TOPBAR_MT_file_import.remove(import_menu)
    del bpy.types.WindowManager.lichtfeld_portal
    if _icons is not None:
        bpy.utils.previews.remove(_icons)
    _icons = None
    _generation = -1
    _rows = None
    _thumbnail = ""
    _identity = None
    _last_revision = -1
    _last_online = None
    _refresh_wanted = False
    _import_cancel = None
    _popup_region = None
