# SPDX-FileCopyrightText: 2026 LichtFeld Studio Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Portal Gallery controls; network, files and decoding live on workers."""
import time
import queue
from pathlib import Path
import bpy
import bpy.utils.previews
from bpy.props import CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix
from . import capture_preview, preview_scene, ui_runtime, rig_edit
from .session_geometry import RDF_TO_BLENDER
from .portal_runtime import Runtime
from lichtfeld_portal.path_io import native_path

_runtime = None
_icons = None
_generation = -1
_rows = None
_stage = None
_destination = None
_pending = None
_identity = None
_last_revision = -1
_last_online = None
_refresh_wanted = False
_frame = None
_import_cancel = None
_popup_region = None
_thumbnail_attempted = set()
_page_signature = None
GALLERY_WIDTH = 900
GALLERY_COLUMNS = 3
PAGE_SIZE = 2 * GALLERY_COLUMNS
IMAGE_ICON = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items["IMAGE_DATA"].value
ACCOUNT_ERRORS = {
    "sign_in_unavailable": "The Portal is temporarily unavailable. Try again.",
    "access_denied": "The account connection was denied in the Portal.",
    "expired_token": "The sign-in code expired. Connect again to get a new code.",
    "invalid_grant": "The sign-in request is no longer valid. Connect again.",
    "invalid_token": "Your Portal session has expired. Connect your account again.",
    "portal_origin_mismatch": "This account was connected to a different Portal. Connect again.",
    "unsafe_portal_url": "The Portal address is not trusted. Check your Portal configuration.",
    "connection_storage_failed": "Blender could not save the Portal connection. Check configuration folder permissions.",
    "local_credentials_removal_failed": "Blender could not remove the saved connection. Check configuration folder permissions and sign out again.",
}
_view_signature = None
_view = None


def state():
    return _runtime.snapshot() if _runtime else {"phase": "idle", "rows": (), "message": "", "progress": 0}


def brand_icon():
    return _icons["lichtfeld"].icon_id if _icons and "lichtfeld" in _icons else 0


def clear_thumbnails():
    for key in tuple(_icons):
        if key != "lichtfeld":
            del _icons[key]


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


def gallery_page(context):
    global _view_signature, _view
    settings = context.window_manager.lichtfeld_portal
    rows = settings.rows
    signature = (_generation, len(rows), settings.search, settings.page)
    if signature == _view_signature:
        return _view
    flags = bpy.types.UI_UL_list.filter_items_by_name(settings.search, 1, rows, "title") if settings.search else None
    indices = [i for i in range(len(rows)) if flags is None or flags[i]]
    pages = max(1, (len(indices) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(settings.page - 1, pages - 1)
    _view_signature = signature
    _view = indices[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], len(indices), page, pages
    return _view


def search_changed(settings, context):
    settings.page = 1


def page_changed(settings, context):
    pages = gallery_page(context)[3]
    if settings.page > pages:
        settings.page = pages


def import_target(scene):
    if capture_preview.is_preview(scene):
        return capture_preview.source_scene(scene)
    if preview_scene.is_preview(scene):
        return preview_scene.source_scene(scene)
    return scene


def select_workspace(context):
    """Open an existing capture/job scene in its workflow after loading a file."""
    if context.window_manager.is_property_set("lichtfeld_workspace"):
        return  # Keep the current workspace until Blender replaces its UI state.
    settings = context.scene.lichtfeld_training
    training = (settings.job_directory or settings.attached_session or settings.running or
                settings.camera_collection or settings.dataset_mode == "REUSE" or
                capture_preview.is_preview(context.scene) or preview_scene.is_preview(context.scene))
    if training:
        context.window_manager.lichtfeld_workspace = "TRAINING"


def import_steps(scene, identifier, count, fields):
    if not bpy.app.background and bpy.ops.ed.undo_push.poll():
        bpy.ops.ed.undo_push(message="Before Portal import")
    cloud = yield from ui_runtime.pointcloud_steps(fields, count, "Portal splat")
    committed = False
    obj = None
    try:
        title = next((row["title"] for row in state()["rows"] if row["id"] == identifier), "Portal splat")
        obj = bpy.data.objects.new(title, cloud)
        obj.matrix_world = Matrix(RDF_TO_BLENDER)
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
    global _page_signature, _popup_region
    global _stage, _pending, _rows, _generation, _identity, _last_revision, _destination
    global _last_online, _refresh_wanted, _frame
    if _runtime is None:
        select_workspace(bpy.context)
        runtime()
        return 0.2
    online = bpy.app.online_access
    if online and _last_online is False:
        _thumbnail_attempted.clear()
        _refresh_wanted = True
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
            clear_thumbnails()
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
            expected_identity, identifier, path = value
            if expected_identity != identity:
                continue
            try:
                if identifier in _icons:
                    _icons[identifier].reload()
                else:
                    _icons.load(identifier, native_path(path), "IMAGE")
            except (OSError, RuntimeError):
                pass  # A missing thumbnail must not prevent importing the asset.
        elif kind == "cache_cleared":
            clear_thumbnails()
            _thumbnail_attempted.clear()
    snapshot = _runtime.snapshot()
    if _import_cancel and _import_cancel.is_set():
        cancel_import()
    if snapshot["phase"] == "ready" and _runtime.cancel.is_set():
        _runtime.update(phase="canceled", message="Import canceled")
    if snapshot["generation"] != _generation:
        _generation = snapshot["generation"]
        bpy.context.window_manager.lichtfeld_portal.rows.clear()
        bpy.context.window_manager.lichtfeld_portal.page = 1
        _rows = iter(snapshot["rows"] if snapshot.get("rows_identity") == identity else ())
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
    settings = bpy.context.window_manager.lichtfeld_portal
    indices, _, page, _ = gallery_page(bpy.context)
    signature = (_generation, settings.search, page)
    if signature != _page_signature:
        _page_signature = signature
        _thumbnail_attempted.clear()
    visible = {settings.rows[index].identifier for index in indices}
    for key in tuple(_icons):
        if key != "lichtfeld" and key not in visible:
            del _icons[key]
    if online and _popup_region is not None and snapshot["phase"] in ("idle", "failed", "canceled"):
        for index in indices:
            identifier = settings.rows[index].identifier
            if identifier not in _thumbnail_attempted:
                if _runtime.start("thumbnail", identifier):
                    _thumbnail_attempted.add(identifier)
                break
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
            message = "Not enough memory to add this asset to Blender" if isinstance(error, MemoryError) else str(error)
            _runtime.update(phase="failed", message=message)
    if snapshot["revision"] != _last_revision or rows_changed:
        _last_revision = snapshot["revision"]
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("VIEW_3D", "PREFERENCES"):
                    area.tag_redraw()
        if _popup_region is not None:
            try:
                _popup_region.tag_refresh_ui()
            except ReferenceError:
                _popup_region = None
    return 0.02 if _stage is not None else (0.1 if _popup_region is not None else 0.5)


class LF_PortalRow(bpy.types.PropertyGroup):
    identifier: StringProperty(options={"SKIP_SAVE"})
    title: StringProperty(options={"SKIP_SAVE"})
    description: StringProperty(options={"SKIP_SAVE"})


class LF_PortalState(bpy.types.PropertyGroup):
    rows: CollectionProperty(type=LF_PortalRow, options={"SKIP_SAVE"})
    search: StringProperty(name="Search projects", description="Filter your Portal projects by title", update=search_changed, options={"SKIP_SAVE"})
    page: IntProperty(name="Gallery page", description="Browse another page of your Portal projects", min=1, default=1, update=page_changed, options={"SKIP_SAVE"})


def account_controls(layout):
    snapshot = state()
    account = snapshot.get("account")
    if not bpy.app.online_access:
        layout.label(text="Blender online access is disabled", icon="INFO")
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_action", text="Open network preferences", icon="PREFERENCES").action = "network_preferences"
    if account and account.signed_in:
        row = layout.split(factor=0.7)
        row.label(text=account.display_name, icon="USER")
        row.enabled = snapshot["phase"] in ("idle", "failed", "canceled", "thumbnail")
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_action", text="Sign out").action = "sign_out"
    elif bpy.app.online_access and account and account.linking:
        layout.label(text="Approve the connection in your browser", icon="URL")
        layout.label(text="Code: " + account.user_code)
        row = layout.row(align=True)
        row.operator("lichtfeld.portal_action", text="Open browser", icon="URL").action = "browser"
        row.operator("lichtfeld.portal_action", text="Copy code", icon="COPYDOWN").action = "copy"
        layout.operator("lichtfeld.portal_action", text="Cancel connection").action = "cancel"
    elif bpy.app.online_access:
        layout.operator("lichtfeld.portal_action", text="Connect account", icon="USER").action = "connect"
    if account and account.error and not account.membership_required:
        from .ui_text import draw_paragraph
        region = bpy.context.region_popup or bpy.context.region
        draw_paragraph(layout, ACCOUNT_ERRORS.get(account.error, "Could not connect to the Portal. Try connecting again."),
                       region.width if region else GALLERY_WIDTH, bpy.context.preferences.system.ui_scale)
    if account and account.membership_required:
        layout.label(text="This account needs access to LichtFeld Portal", icon="INFO")


def gallery_controls(layout, context):
    from .ui_text import draw_paragraph
    heading = layout.row(align=True)
    heading.label(text="LichtFeld · Portal Gallery", icon_value=brand_icon())
    heading.operator_context = "INVOKE_DEFAULT"
    heading.operator("lichtfeld.setup", text="", icon="PREFERENCES")
    account_controls(layout)
    snapshot = state()
    account = snapshot.get("account")
    if not bpy.app.online_access or not account or not account.signed_in:
        if snapshot["message"] and not (account and account.error):
            draw_paragraph(layout, snapshot["message"], 850, context.preferences.system.ui_scale)
        return
    busy = snapshot["phase"] not in ("idle", "failed", "canceled", "thumbnail")
    settings = context.window_manager.lichtfeld_portal
    configured = bool(context.preferences.addons[__package__].preferences.backend_path.strip())
    if not configured:
        layout.label(text="Choose LichtFeld Studio in Settings to import assets", icon="INFO")
        layout.operator("lichtfeld.setup", text="Configure LichtFeld Studio…", icon="PREFERENCES")
    indices, total, page, pages = gallery_page(context)
    row = layout.row(align=True)
    row.prop(settings, "search", text="", icon="VIEWZOOM")
    refresh = row.row(align=True)
    refresh.enabled = not busy
    refresh.operator("lichtfeld.portal_action", text="", icon="FILE_REFRESH").action = "refresh"
    row = layout.row()
    row.label(text=(f"{total:,} of {len(settings.rows):,} projects match" if settings.search else
                    f"{total:,} project{'s' if total != 1 else ''} on your Portal"))
    if pages > 1:
        row.prop(settings, "page", text=f"Page / {pages}")
    target = import_target(context.scene)
    reason = available(target) if target else "The source scene is no longer available."
    if indices:
        grid = layout.grid_flow(row_major=True, columns=GALLERY_COLUMNS, even_columns=True, even_rows=True, align=False)
        for index in indices:
            item = settings.rows[index]
            card = grid.box()
            card.template_icon(icon_value=_icons[item.identifier].icon_id if item.identifier in _icons else IMAGE_ICON,
                               scale=GALLERY_WIDTH / GALLERY_COLUMNS / 20 - 1.5)
            card.label(text=item.title, icon="OUTLINER_OB_POINTCLOUD")
            if item.description:
                draw_paragraph(card, item.description[:120], 260, context.preferences.system.ui_scale)
            action = card.row()
            action.enabled = target is not None and not busy and configured
            button = action.operator("lichtfeld.portal_action", text="Queue import" if reason else "Import", icon="IMPORT")
            button.action, button.identifier = "import", item.identifier
        # Keep partial rows at the same card width as a full gallery page.
        for _ in range((-len(indices)) % GALLERY_COLUMNS):
            grid.column().label(text="")
    else:
        layout.label(text="No matching projects" if settings.search else "Your Portal gallery is empty", icon="INFO")
    if target:
        layout.label(text="Import into: " + target.name, icon="SCENE_DATA")
    if reason:
        draw_paragraph(layout, reason, 850, context.preferences.system.ui_scale)
    if busy:
        phase = {"import": "Downloading", "checking": "Checking download", "converting": "Preparing asset", "decoding": "Preparing Blender data", "importing": "Adding to scene", "refresh": "Refreshing gallery", "ready": "Waiting to import"}.get(snapshot["phase"], "Working")
        layout.progress(factor=snapshot["progress"], type="BAR", text=phase)
        if snapshot["phase"] != "sign_out":
            layout.operator("lichtfeld.portal_action", text="Cancel", icon="CANCEL").action = "cancel"
    if snapshot["message"]:
        draw_paragraph(layout, snapshot["message"], 850, context.preferences.system.ui_scale)
    if snapshot.get("total_bytes"):
        layout.label(text=f"Download size: {snapshot['total_bytes'] / (1024 * 1024):.1f} MiB")
    if snapshot.get("details"):
        layout.operator("lichtfeld.portal_action", text="Copy error details", icon="COPYDOWN").action = "copy_error"
    if context.active_object and context.active_object.get("lichtfeld_portal_scene"):
        layout.operator("lichtfeld.portal_frame", icon="VIEWZOOM")


class LF_OT_portal_action(bpy.types.Operator):
    bl_idname = "lichtfeld.portal_action"
    bl_label = "Portal Gallery action"
    bl_description = "Connect, browse or import from your LichtFeld Portal gallery without changing the training job"
    action: StringProperty(options={"SKIP_SAVE"})
    identifier: StringProperty(name="Portal project", description="The project chosen for import", options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return {
            "import": "Download and add the visible splats to your scene. Requires LichtFeld Studio for headless conversion; existing objects and training results stay intact",
            "refresh": "Check your Portal gallery for available assets",
            "connect": "Approve Blender's Portal connection in your browser; no password is entered in Blender",
            "cancel": "Cancel the pending connection or asset import; this does not stop training",
            "browser": "Open the Portal approval page in your browser",
            "copy": "Copy the device approval code to the clipboard",
            "sign_out": "Sign out of the Portal in Blender; LichtFeld Studio and imported objects are unaffected. Offline sign-out removes local credentials only",
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
        # Native RNA stores an explicit choice; delayed job recovery respects it.
        context.window_manager.lichtfeld_workspace = context.window_manager.lichtfeld_workspace
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
        if not bpy.app.online_access and self.action != "sign_out":
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
            item = next((row for row in context.window_manager.lichtfeld_portal.rows if row.identifier == self.identifier), None)
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
            if self.action == "sign_out":
                runtime().start(self.action, bpy.app.online_access)
            else:
                runtime().start(self.action)
        return {"FINISHED"}


class LF_OT_portal_browser(bpy.types.Operator):
    bl_idname = "lichtfeld.portal_gallery"
    bl_label = "Portal Gallery"
    bl_description = "Browse your LichtFeld Portal assets and add splats to the current scene"

    def invoke(self, context, event):
        context.window_manager.lichtfeld_workspace = context.window_manager.lichtfeld_workspace
        runtime()
        return context.window_manager.invoke_popup(self, width=GALLERY_WIDTH)

    def draw(self, context):
        global _popup_region
        _popup_region = context.region_popup or context.region
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
    bl_idname = "LF_PT_lichtfeld"
    bl_label = "LichtFeld"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LichtFeld"
    bl_order = -1
    bl_ui_units_x = 20

    def draw_header(self, context):
        self.layout.label(text="", icon_value=brand_icon())

    def draw_header_preset(self, context):
        self.layout.operator("lichtfeld.setup", text="", icon="PREFERENCES")

    def draw(self, context):
        self.layout.prop(context.window_manager, "lichtfeld_workspace", expand=True)
        if context.window_manager.lichtfeld_workspace != "PORTAL":
            from . import draw_training
            draw_training(self, context)
            return
        if capture_preview.is_preview(context.scene):
            self.layout.label(text="Temporary capture preview", icon="INFO")
            self.layout.operator("lichtfeld.close_capture_framing", text="Back to source scene", icon="BACK")
        elif preview_scene.is_preview(context.scene):
            self.layout.label(text="Temporary trainer color view", icon="INFO")
            self.layout.operator("lichtfeld.trainer_color_view", text="Back to source scene", icon="BACK")
        row = self.layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator("lichtfeld.portal_gallery", text="Browse gallery…")
        account_controls(self.layout)
        snapshot = state()
        account = snapshot.get("account")
        if snapshot["message"] and not (account and account.error):
            from .ui_text import draw_paragraph
            draw_paragraph(self.layout, snapshot["message"], context.region.width, context.preferences.system.ui_scale)


CLASSES = (LF_PortalRow, LF_PortalState, LF_OT_portal_action, LF_OT_portal_browser, LF_OT_portal_frame, LF_PT_portal)


def import_menu(self, context):
    row = self.layout.row()
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("lichtfeld.portal_gallery", text="LichtFeld Portal Gallery…", icon_value=brand_icon())


def register():
    global _icons
    _icons = bpy.utils.previews.new()
    _icons.load("lichtfeld", native_path(Path(__file__).with_name("assets") / "lichtfeld-icon.png"), "IMAGE")
    bpy.types.WindowManager.lichtfeld_portal = PointerProperty(type=LF_PortalState, options={"SKIP_SAVE"})
    bpy.types.WindowManager.lichtfeld_workspace = EnumProperty(name="LichtFeld workspace",
        description="Choose Portal assets or the scene capture and training workflow",
        items=(("PORTAL", "Portal", "Browse and import your Portal assets", "ASSET_MANAGER", 0),
               ("TRAINING", "Capture / Train", "Prepare cameras and datasets, train or view a trainer", "CAMERA_DATA", 1)),
        default="PORTAL", options={"SKIP_SAVE"})
    bpy.types.TOPBAR_MT_file_import.append(import_menu)
    if not bpy.app.background:
        bpy.app.timers.register(tick, first_interval=0.2, persistent=True)


def unregister():
    global _runtime, _icons, _generation, _rows, _identity, _last_revision
    global _last_online, _refresh_wanted, _import_cancel
    global _popup_region, _page_signature, _view_signature, _view
    invalidate()
    if _runtime:
        _runtime.close()
        _runtime = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    bpy.types.TOPBAR_MT_file_import.remove(import_menu)
    del bpy.types.WindowManager.lichtfeld_portal
    del bpy.types.WindowManager.lichtfeld_workspace
    if _icons is not None:
        bpy.utils.previews.remove(_icons)
    _icons = None
    _generation = -1
    _rows = None
    _identity = None
    _last_revision = -1
    _last_online = None
    _refresh_wanted = False
    _import_cancel = None
    _popup_region = None
    _page_signature = None
    _view_signature = None
    _view = None
    _thumbnail_attempted.clear()
