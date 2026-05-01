bl_info = {
    "name": "Quality Audit Checker",
    "author": "OpenAI",
    "version": (1, 6, 5),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > QA Audit",
    "description": "Checks Blender files for quality and consistency with inline fixes.",
    "category": "Object",
}

import bpy
import re
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList


def object_transform_applied(obj, tolerance=1e-5):
    loc_ok = obj.location.length <= tolerance
    rot_ok = all(abs(v) <= tolerance for v in obj.rotation_euler)
    scale_ok = all(abs(v - 1.0) <= tolerance for v in obj.scale)
    return loc_ok and rot_ok and scale_ok, loc_ok, rot_ok, scale_ok


def is_skeletal_mesh(obj):
    if obj.type != 'MESH':
        return False
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and getattr(mod, 'object', None) is not None:
            return True
    if obj.parent and obj.parent.type == 'ARMATURE':
        return True
    return False


def expected_mesh_prefix(obj):
    return 'SK_' if is_skeletal_mesh(obj) else 'SM_'


def mesh_has_expected_prefix(obj):
    if obj.type != 'MESH':
        return True
    return obj.name.startswith(expected_mesh_prefix(obj))


def uv_name_issues(obj, pattern):
    issues = []
    if obj.type != 'MESH' or obj.data is None:
        return issues
    try:
        regex = re.compile(pattern)
    except re.error:
        return [("uv_names", f"Invalid UV regex: {pattern}")]
    for uv in obj.data.uv_layers:
        if not uv.name:
            issues.append(("uv_names", "Unnamed UV channel"))
        elif not regex.match(uv.name):
            issues.append(("uv_names", f"UV '{uv.name}' does not match '{pattern}'"))
    return issues


def object_mesh_name_match(obj):
    return obj.type != 'MESH' or obj.data is None or obj.name == obj.data.name


def material_prefix_issues(obj):
    issues = []
    if obj.type != 'MESH' or obj.data is None:
        return issues
    for slot in obj.material_slots:
        material = slot.material
        if material and not material.name.startswith("MT_"):
            issues.append(("material_prefix", f"Material '{material.name}' missing MT_ prefix"))
    return issues


def orphan_data_summary():
    categories = [
        ("meshes", bpy.data.meshes),
        ("materials", bpy.data.materials),
        ("images", bpy.data.images),
        ("textures", bpy.data.textures),
        ("actions", bpy.data.actions),
        ("armatures", bpy.data.armatures),
        ("collections", bpy.data.collections),
        ("curves", bpy.data.curves),
        ("cameras", bpy.data.cameras),
        ("lights", bpy.data.lights),
        ("node_groups", bpy.data.node_groups),
    ]
    issues = []
    for label, datablocks in categories:
        count = 0
        for datablock in datablocks:
            if getattr(datablock, 'users', 0) == 0 and not getattr(datablock, 'use_fake_user', False):
                count += 1
        if count:
            issues.append(("not_purged", f"{label}: {count} orphan(s)"))
    return issues


def packed_data_issues():
    issues = []
    for image in bpy.data.images:
        if image.source == 'FILE' and image.filepath and not image.packed_file:
            issues.append(("not_packed", f"Image not packed: {image.name}"))
    for sound in bpy.data.sounds:
        if sound.filepath and not sound.packed_file:
            issues.append(("not_packed", f"Sound not packed: {sound.name}"))
    for font in bpy.data.fonts:
        if getattr(font, 'filepath', '') and not getattr(font, 'packed_file', None):
            issues.append(("not_packed", f"Font not packed: {font.name}"))
    for clip in bpy.data.movieclips:
        if clip.filepath and not clip.packed_file:
            issues.append(("not_packed", f"Movie clip not packed: {clip.name}"))
    for library in bpy.data.libraries:
        issues.append(("not_packed", f"Linked library not packable: {library.filepath}"))
    return issues


def ensure_material_prefix(material):
    if material and not material.name.startswith("MT_"):
        material.name = f"MT_{material.name}"
        return True
    return False


def active_mesh(context):
    obj = context.active_object
    if obj and obj.type == 'MESH':
        return obj
    return None


def select_object_in_viewport(context, obj_name, focus=False):
    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return False
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if focus:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is not None:
                    with context.temp_override(area=area, region=region):
                        try:
                            bpy.ops.view3d.view_selected(use_all_regions=False)
                        except RuntimeError:
                            pass
                break
    return True


def refresh_counts(scene):
    pass_count = 0
    fail_count = 0
    file_has_issue = any(item.item_type == 'FILE' for item in scene.qa_audit_results)
    if file_has_issue:
        fail_count += 1
    else:
        pass_count += 1
    mesh_names_with_issues = {item.name for item in scene.qa_audit_results if item.item_type == 'MESH'}
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if obj.name in mesh_names_with_issues:
            fail_count += 1
        else:
            pass_count += 1
    scene.qa_pass_count = pass_count
    scene.qa_fail_count = fail_count


def ignored_codes_for_item(scene, item_name, item_type):
    key = f"{item_type}:{item_name}"
    for entry in scene.qa_ignored_issues:
        if entry.key == key:
            return set(x for x in entry.value.split("|") if x)
    return set()


def set_ignored_codes_for_item(scene, item_name, item_type, codes):
    key = f"{item_type}:{item_name}"
    found_index = -1
    for i, entry in enumerate(scene.qa_ignored_issues):
        if entry.key == key:
            found_index = i
            break
    if codes:
        if found_index == -1:
            entry = scene.qa_ignored_issues.add()
        else:
            entry = scene.qa_ignored_issues[found_index]
        entry.key = key
        entry.value = "|".join(sorted(codes))
    elif found_index != -1:
        scene.qa_ignored_issues.remove(found_index)



# ---------------------------------------------------------------------------
# Unit-length helpers
# ---------------------------------------------------------------------------

_QA_UNIT_ITEMS = [
    ('INCHES',      'Inches',      'Imperial inches (sets system to Imperial)'),
    ('CENTIMETERS', 'Centimeters', 'Metric centimetres (sets system to Metric)'),
    ('METERS',      'Meters',      'Metric metres (sets system to Metric)'),
    ('FEET',        'Feet',        'Imperial feet (sets system to Imperial)'),
]

# Blender requires both length_unit AND system to be set together so that
# Feet / Inches actually take effect in the viewport.
_QA_UNIT_BLENDER = {
    'INCHES':      ('IMPERIAL', 'INCHES'),
    'CENTIMETERS': ('METRIC',   'CENTIMETERS'),
    'METERS':      ('METRIC',   'METERS'),
    'FEET':        ('IMPERIAL', 'FEET'),
}
_BLENDER_TO_QA = {lu: qa for qa, (_, lu) in _QA_UNIT_BLENDER.items()}


def _qa_unit_update(self, context):
    system, length_unit = _QA_UNIT_BLENDER.get(self.qa_unit_length, ('METRIC', 'METERS'))
    us = context.scene.unit_settings
    if us.system != system:
        us.system = system
    if us.length_unit != length_unit:
        us.length_unit = length_unit


# ---------------------------------------------------------------------------
# Post-fix viewport helper
# ---------------------------------------------------------------------------

def _select_result_in_viewport(scene, context):
    """After a fix removes an entry, select whatever result is now current."""
    if not (0 <= scene.qa_audit_index < len(scene.qa_audit_results)):
        return
    item = scene.qa_audit_results[scene.qa_audit_index]
    if item.item_type == 'MESH':
        select_object_in_viewport(context, item.name)


class QA_IgnoredIssue(PropertyGroup):
    key: StringProperty(name="Key")
    value: StringProperty(name="Value")


class QA_AuditResult(PropertyGroup):
    item_type: StringProperty(name="Item Type")
    name: StringProperty(name="Name")
    issues: StringProperty(name="Issues")
    issue_codes: StringProperty(name="Issue Codes")


class QA_AuditSettings(PropertyGroup):
    uv_name_regex: StringProperty(name="UV Name Regex", default=r"^UV_.*")
    check_transforms: BoolProperty(name="Applied transforms", default=True)
    check_uv_names: BoolProperty(name="UV channel names", default=True)
    check_object_mesh_name: BoolProperty(name="Object and mesh names match", default=True)
    check_purged: BoolProperty(name="File is purged", default=True)
    check_packed: BoolProperty(name="All elements are packed", default=True)
    check_mesh_prefix: BoolProperty(name="Mesh prefixes SM_ / SK_", default=True)
    check_material_prefix: BoolProperty(name="Material prefix MT_", default=True)
    uv_fix_base_name: StringProperty(name="UV Rename Base", default="UV_Map")


def compute_object_issues(scene, obj):
    settings = scene.qa_audit_settings
    ignored = ignored_codes_for_item(scene, obj.name, 'MESH')
    issues = []
    if settings.check_transforms:
        all_ok, loc_ok, rot_ok, scale_ok = object_transform_applied(obj)
        if not all_ok and 'transforms' not in ignored:
            parts = []
            if not loc_ok:
                parts.append("location")
            if not rot_ok:
                parts.append("rotation")
            if not scale_ok:
                parts.append("scale")
            issues.append(("transforms", "Transforms not applied: " + ", ".join(parts)))
    if settings.check_uv_names:
        issues.extend([x for x in uv_name_issues(obj, settings.uv_name_regex) if x[0] not in ignored])
    if settings.check_object_mesh_name and not object_mesh_name_match(obj) and 'object_mesh_name' not in ignored:
        issues.append(("object_mesh_name", f"Object '{obj.name}' vs Mesh '{obj.data.name}'"))
    if settings.check_mesh_prefix and not mesh_has_expected_prefix(obj) and 'mesh_prefix' not in ignored:
        issues.append(("mesh_prefix", f"Expected prefix {expected_mesh_prefix(obj)}"))
    if settings.check_material_prefix:
        issues.extend([x for x in material_prefix_issues(obj) if x[0] not in ignored])
    return issues


def upsert_result_for_object(scene, obj, context=None):
    existing_index = -1
    for index, item in enumerate(scene.qa_audit_results):
        if item.item_type == 'MESH' and item.name == obj.name:
            existing_index = index
            break
    issues = compute_object_issues(scene, obj)
    if issues:
        if existing_index == -1:
            item = scene.qa_audit_results.add()
            existing_index = len(scene.qa_audit_results) - 1
        else:
            item = scene.qa_audit_results[existing_index]
        item.item_type = 'MESH'
        item.name = obj.name
        item.issues = "\n".join(text for _, text in issues)
        item.issue_codes = "\n".join(code for code, _ in issues)
        scene.qa_audit_index = existing_index
    else:
        if existing_index != -1:
            scene.qa_audit_results.remove(existing_index)
            if len(scene.qa_audit_results) == 0:
                scene.qa_audit_index = 0
            else:
                scene.qa_audit_index = min(existing_index, len(scene.qa_audit_results) - 1)
            # Sync viewport to whatever result is now highlighted
            if context is not None:
                _select_result_in_viewport(scene, context)
    refresh_counts(scene)


def rebuild_file_result(scene):
    existing_file_index = -1
    for index, item in enumerate(scene.qa_audit_results):
        if item.item_type == 'FILE':
            existing_file_index = index
            break
    file_name = bpy.path.basename(bpy.data.filepath) or 'Unsaved'
    ignored = ignored_codes_for_item(scene, file_name, 'FILE')
    issues = []
    if scene.qa_audit_settings.check_purged:
        orphan_issues = orphan_data_summary()
        if orphan_issues and 'not_purged' not in ignored:
            issues.append(("not_purged", "File not fully purged"))
            issues.extend(orphan_issues)
    if scene.qa_audit_settings.check_packed and 'not_packed' not in ignored:
        issues.extend(packed_data_issues())
    if issues:
        if existing_file_index == -1:
            item = scene.qa_audit_results.add()
        else:
            item = scene.qa_audit_results[existing_file_index]
        item.item_type = 'FILE'
        item.name = file_name
        item.issues = "\n".join(text for _, text in issues)
        item.issue_codes = "\n".join(code for code, _ in issues)
    elif existing_file_index != -1:
        scene.qa_audit_results.remove(existing_file_index)
    refresh_counts(scene)


def get_detail_target(scene):
    if 0 <= scene.qa_audit_index < len(scene.qa_audit_results):
        return scene.qa_audit_results[scene.qa_audit_index]
    return None


class QA_OT_RunAudit(Operator):
    bl_idname = "qa_audit.run"
    bl_label = "Run Quality Audit"
    bl_description = "Scan every mesh in the scene and rebuild the full issue list"

    def execute(self, context):
        scene = context.scene
        last_active_name = context.active_object.name if context.active_object else None
        scene.qa_audit_results.clear()
        rebuild_file_result(scene)
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                issues = compute_object_issues(scene, obj)
                if issues:
                    item = scene.qa_audit_results.add()
                    item.item_type = 'MESH'
                    item.name = obj.name
                    item.issues = "\n".join(text for _, text in issues)
                    item.issue_codes = "\n".join(code for code, _ in issues)
        refresh_counts(scene)
        if last_active_name:
            obj = bpy.data.objects.get(last_active_name)
            if obj:
                for selected in context.selected_objects:
                    selected.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
                for index, item in enumerate(scene.qa_audit_results):
                    if item.item_type == 'MESH' and item.name == last_active_name:
                        scene.qa_audit_index = index
                        break
        elif scene.qa_audit_results:
            scene.qa_audit_index = 0
        return {'FINISHED'}


class QA_OT_SelectAuditItem(Operator):
    bl_idname = "qa_audit.select_item"
    bl_label = "Select Item"
    bl_description = "Select this item in the 3D viewport; use the magnifier icon to also frame it"

    item_name: StringProperty()
    item_type: StringProperty()
    item_index: IntProperty(default=-1)
    focus: BoolProperty(default=False)

    def execute(self, context):
        scene = context.scene
        if self.item_index >= 0:
            scene.qa_audit_index = self.item_index
        if self.item_type == 'MESH':
            select_object_in_viewport(context, self.item_name, focus=self.focus)
        else:
            if context.mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode='OBJECT')
                except RuntimeError:
                    pass
            for selected in context.selected_objects:
                selected.select_set(False)
            context.view_layer.objects.active = None
        return {'FINISHED'}


class QA_OT_IgnoreIssue(Operator):
    bl_idname = "qa_audit.ignore_issue"
    bl_label = "Ignore Issue"
    bl_description = "Suppress this specific issue for this object so it no longer appears in results"

    item_name: StringProperty()
    item_type: StringProperty()
    issue_code: StringProperty()

    def execute(self, context):
        scene = context.scene
        ignored = ignored_codes_for_item(scene, self.item_name, self.item_type)
        ignored.add(self.issue_code)
        set_ignored_codes_for_item(scene, self.item_name, self.item_type, ignored)
        if self.item_type == 'FILE':
            rebuild_file_result(scene)
        else:
            obj = bpy.data.objects.get(self.item_name)
            if obj:
                upsert_result_for_object(scene, obj)
        return {'FINISHED'}


def _resolve_target_mesh(context):
    """Return the mesh object for the currently-highlighted result entry.
    Falls back to the viewport active object so operators still work when
    called from outside the QA panel."""
    scene = context.scene
    item = scene.qa_audit_results[scene.qa_audit_index] if (
        0 <= scene.qa_audit_index < len(scene.qa_audit_results)
    ) else None
    if item and item.item_type == 'MESH':
        obj = bpy.data.objects.get(item.name)
        if obj and obj.type == 'MESH':
            # Make sure it is the active object so transform_apply works
            if context.view_layer.objects.active != obj:
                for sel in context.selected_objects:
                    sel.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
            return obj
    return active_mesh(context)


class QA_OT_FixLocation(Operator):
    bl_idname = "qa_audit.fix_location"
    bl_label = "Apply Location"
    bl_description = "Zero out the object's world-space location by baking it into the mesh data"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj:
            return {'CANCELLED'}
        bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_FixRotation(Operator):
    bl_idname = "qa_audit.fix_rotation"
    bl_label = "Apply Rotation"
    bl_description = "Zero out the object's rotation by baking it into the mesh data"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj:
            return {'CANCELLED'}
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_FixScale(Operator):
    bl_idname = "qa_audit.fix_scale"
    bl_label = "Apply Scale"
    bl_description = "Reset the object's scale to (1, 1, 1) by baking it into the mesh data"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj:
            return {'CANCELLED'}
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_FixObjectMeshName(Operator):
    bl_idname = "qa_audit.fix_object_mesh_name"
    bl_label = "Match Mesh Name to Object"
    bl_description = "Copy the object's name onto its mesh data-block so both names match"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj or obj.data is None:
            return {'CANCELLED'}
        obj.data.name = obj.name
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_FixAllObjectMeshNames(Operator):
    bl_idname = "qa_audit.fix_all_object_mesh_names"
    bl_label = "Match All Mesh-Name to Object"
    bl_description = "For every mesh in the scene, copy the object name onto its mesh data-block"

    def execute(self, context):
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.data is not None and obj.name != obj.data.name:
                obj.data.name = obj.name
                upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_AddMTPrefixActive(Operator):
    bl_idname = "qa_audit.add_mt_active"
    bl_label = "Add MT_ to Materials"
    bl_description = "Prefix all materials on this object with 'MT_' to match the naming convention"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj:
            return {'CANCELLED'}
        for slot in obj.material_slots:
            ensure_material_prefix(slot.material)
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_RenameUVActive(Operator):
    bl_idname = "qa_audit.rename_uv_active"
    bl_label = "Rename UVs"
    bl_description = "Rename all UV channels on this object to match the UV naming convention (e.g. UV_0, UV_1)"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj or obj.data is None:
            return {'CANCELLED'}
        base_name = context.scene.qa_audit_settings.uv_fix_base_name
        for index, uv in enumerate(obj.data.uv_layers):
            uv.name = base_name if index == 0 else f"{base_name}_{index}"
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_FixMeshPrefixActive(Operator):
    bl_idname = "qa_audit.fix_mesh_prefix_active"
    bl_label = "Fix Prefix"
    bl_description = "Rename this object to use the correct SM_ (static) or SK_ (skeletal) prefix"

    def execute(self, context):
        obj = _resolve_target_mesh(context)
        if not obj:
            return {'CANCELLED'}
        expected = expected_mesh_prefix(obj)
        base_name = obj.name
        for prefix in ("SM_", "SK_"):
            if base_name.startswith(prefix):
                base_name = base_name[len(prefix):]
                break
        obj.name = expected + base_name
        if obj.data and (obj.data.name.startswith("SM_") or obj.data.name.startswith("SK_")):
            obj.data.name = obj.name
        upsert_result_for_object(context.scene, obj, context)
        return {'FINISHED'}


class QA_OT_PurgeOrphans(Operator):
    bl_idname = "qa_audit.purge_orphans"
    bl_label = "Purge Orphans"
    bl_description = "Remove all unused data-blocks (meshes, materials, images, etc.) from the .blend file"

    def execute(self, context):
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        rebuild_file_result(context.scene)
        return {'FINISHED'}


class QA_OT_PackAll(Operator):
    bl_idname = "qa_audit.pack_all"
    bl_label = "Pack All"
    bl_description = "Embed all external images, sounds, and fonts directly into the .blend file"

    def execute(self, context):
        bpy.ops.file.pack_all()
        rebuild_file_result(context.scene)
        return {'FINISHED'}


class QA_UL_Results(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        op_name = row.operator(
            'qa_audit.select_item',
            text=item.name,
            emboss=False,
            icon='FILE_BLEND' if item.item_type == 'FILE' else 'MESH_CUBE',
        )
        op_name.item_name = item.name
        op_name.item_type = item.item_type
        op_name.item_index = index
        op_name.focus = False
        op_focus = row.operator('qa_audit.select_item', text='', icon='VIEWZOOM')
        op_focus.item_name = item.name
        op_focus.item_type = item.item_type
        op_focus.item_index = index
        op_focus.focus = True


class QA_PT_Panel(Panel):
    bl_label = 'Quality Audit'
    bl_idname = 'QA_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'QA Audit'

    def draw_transform_fix_row(self, box, obj):
        _, loc_ok, rot_ok, scale_ok = object_transform_applied(obj)
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.alert = not loc_ok
        sub.operator('qa_audit.fix_location', icon='EMPTY_AXIS')
        sub = row.row(align=True)
        sub.alert = not rot_ok
        sub.operator('qa_audit.fix_rotation', icon='DRIVER_ROTATIONAL_DIFFERENCE')
        sub = row.row(align=True)
        sub.alert = not scale_ok
        sub.operator('qa_audit.fix_scale', icon='FULLSCREEN_ENTER')

    def draw_issue(self, layout, code, text, context, item_name, item_type):
        box = layout.box()
        header = box.row(align=True)
        header.alert = True
        header.label(text=text, icon='ERROR')
        ignore = header.operator('qa_audit.ignore_issue', text='', icon='X')
        ignore.item_name = item_name
        ignore.item_type = item_type
        ignore.issue_code = code

        # Resolve the mesh object directly from the result-item name so fix
        # buttons appear regardless of what is currently active in the viewport.
        obj = bpy.data.objects.get(item_name) if item_type == 'MESH' else None
        if obj and obj.type != 'MESH':
            obj = None
        if code == 'transforms' and obj:
            self.draw_transform_fix_row(box, obj)
        elif code == 'object_mesh_name':
            box.operator('qa_audit.fix_object_mesh_name', icon='OUTLINER_DATA_MESH')
        elif code == 'uv_names':
            settings = context.scene.qa_audit_settings
            box.prop(settings, 'uv_fix_base_name', text='New UV Name')
            box.operator('qa_audit.rename_uv_active', icon='MESH_DATA')
        elif code == 'material_prefix':
            box.operator('qa_audit.add_mt_active', icon='MATERIAL')
        elif code == 'mesh_prefix':
            box.operator('qa_audit.fix_mesh_prefix_active', icon='MESH_DATA')

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        run_box = layout.box()
        run_box.operator('qa_audit.run', text='Run Quality Audit', icon='CHECKMARK')

        summary_box = layout.box()
        row = summary_box.row(align=True)
        row.box().label(text=f"Pass: {scene.qa_pass_count}", icon='CHECKMARK')
        fail_box = row.box()
        fail_box.alert = scene.qa_fail_count > 0
        fail_box.label(text=f"Fail: {scene.qa_fail_count}", icon='ERROR')

        layout.prop(scene, 'qa_show_checks', text='Checks', icon='TRIA_DOWN' if scene.qa_show_checks else 'TRIA_RIGHT')
        if scene.qa_show_checks:
            checks_box = layout.box()
            settings = scene.qa_audit_settings
            col = checks_box.column(align=True)
            col.prop(settings, 'check_transforms')
            col.prop(settings, 'check_uv_names')
            col.prop(settings, 'uv_name_regex')
            col.prop(settings, 'check_object_mesh_name')
            col.prop(settings, 'check_purged')
            col.prop(settings, 'check_packed')
            col.prop(settings, 'check_mesh_prefix')
            col.prop(settings, 'check_material_prefix')

        results_box = layout.box()
        results_box.label(text='Results', icon='VIEWZOOM')
        results_box.template_list('QA_UL_Results', '', scene, 'qa_audit_results', scene, 'qa_audit_index', rows=8)

        detail_target = get_detail_target(scene)
        if detail_target is not None:
            detail_box = layout.box()
            detail_box.label(text=f"Issues: {detail_target.name}", icon='INFO')
            # Issues are stored with real newlines — split on '\n' not '\\n'
            codes = detail_target.issue_codes.split('\n') if detail_target.issue_codes else []
            texts = detail_target.issues.split('\n') if detail_target.issues else []
            for index, text in enumerate(texts):
                if text.strip():
                    code = codes[index] if index < len(codes) else 'unknown'
                    self.draw_issue(detail_box, code, text, context, detail_target.name, detail_target.item_type)

        global_box = layout.box()
        global_box.label(text='Global Fixes', icon='TOOL_SETTINGS')
        global_box.operator('qa_audit.fix_all_object_mesh_names', icon='OUTLINER_DATA_MESH')

        scene_box = layout.box()
        scene_box.label(text='Scene Settings', icon='SCENE_DATA')
        # Show the real decimal frame rate (fps / fps_base) as a read-only label
        actual_fps = scene.render.fps / (scene.render.fps_base or 1.0)
        fps_row = scene_box.row(align=True)
        fps_row.prop(scene.render, 'fps', text='Frame Rate')
        fps_row.label(text=f"= {actual_fps:.6g} fps")
        # Custom enum restricted to the four pipeline units
        scene_box.prop(scene, 'qa_unit_length', text='Unit Length')

        action_box = layout.box()
        action_box.label(text='File Actions', icon='FILE_BLEND')
        row = action_box.row(align=True)
        row.operator('qa_audit.purge_orphans', icon='ORPHAN_DATA')
        row.operator('qa_audit.pack_all', icon='PACKAGE')


@persistent
def qa_selection_sync(scene, depsgraph):
    context = bpy.context
    obj = context.active_object

    if obj is not None and obj.type == 'MESH':
        # A mesh is active — point the list at its result entry (if any)
        for index, item in enumerate(scene.qa_audit_results):
            if item.item_type == 'MESH' and item.name == obj.name:
                if scene.qa_audit_index != index:
                    scene.qa_audit_index = index
                return
    else:
        # Nothing (or a non-mesh) is active — show the FILE result so
        # blend-file issues are always visible when no object is selected.
        for index, item in enumerate(scene.qa_audit_results):
            if item.item_type == 'FILE':
                if scene.qa_audit_index != index:
                    scene.qa_audit_index = index
                return


classes = (
    QA_IgnoredIssue,
    QA_AuditResult,
    QA_AuditSettings,
    QA_OT_RunAudit,
    QA_OT_SelectAuditItem,
    QA_OT_IgnoreIssue,
    QA_OT_FixLocation,
    QA_OT_FixRotation,
    QA_OT_FixScale,
    QA_OT_FixObjectMeshName,
    QA_OT_FixAllObjectMeshNames,
    QA_OT_AddMTPrefixActive,
    QA_OT_RenameUVActive,
    QA_OT_FixMeshPrefixActive,
    QA_OT_PurgeOrphans,
    QA_OT_PackAll,
    QA_UL_Results,
    QA_PT_Panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.qa_audit_results = CollectionProperty(type=QA_AuditResult)
    bpy.types.Scene.qa_audit_index = IntProperty(default=0)
    bpy.types.Scene.qa_audit_settings = PointerProperty(type=QA_AuditSettings)
    bpy.types.Scene.qa_pass_count = IntProperty(default=0)
    bpy.types.Scene.qa_fail_count = IntProperty(default=0)
    bpy.types.Scene.qa_show_checks = BoolProperty(name='Show Checks', default=False)
    bpy.types.Scene.qa_ignored_issues = CollectionProperty(type=QA_IgnoredIssue)
    # Seed the custom unit dropdown from whatever the scene already has
    try:
        _seed = _BLENDER_TO_QA.get(bpy.context.scene.unit_settings.length_unit, 'METERS')
    except Exception:
        _seed = 'METERS'
    bpy.types.Scene.qa_unit_length = EnumProperty(
        name="Unit Length",
        description="Set the scene's unit of length (also updates the system to Imperial or Metric)",
        items=_QA_UNIT_ITEMS,
        default=_seed,
        update=_qa_unit_update,
    )
    if qa_selection_sync not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(qa_selection_sync)


def unregister():
    if qa_selection_sync in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(qa_selection_sync)
    del bpy.types.Scene.qa_ignored_issues
    del bpy.types.Scene.qa_unit_length
    del bpy.types.Scene.qa_show_checks
    del bpy.types.Scene.qa_fail_count
    del bpy.types.Scene.qa_pass_count
    del bpy.types.Scene.qa_audit_settings
    del bpy.types.Scene.qa_audit_index
    del bpy.types.Scene.qa_audit_results
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
