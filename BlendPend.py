bl_info = {
    "name": "Universal Appender",
    "author": "VibeCoded by Beefytime",
    "version": (1, 9, 1),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Append",
    "description": "Staged hierarchical importing, quick filters, and streamlined asset cataloging.",
    "category": "Import-Export",
}

import bpy
import os
import zipfile
import shutil
import re
import uuid
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, PointerProperty, CollectionProperty, IntProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList, Menu

is_updating_hierarchy = False

def update_hierarchy_selection(self, context):
    global is_updating_hierarchy
    if is_updating_hierarchy: 
        return
        
    settings = context.scene.append_everything_settings
    idx = -1
    for i, item in enumerate(settings.staged_items):
        if item.as_pointer() == self.as_pointer():
            idx = i
            break
            
    if idx != -1 and self.item_type == 'COLLECTION':
        is_updating_hierarchy = True
        my_indent = self.indent
        new_state = self.use
        for i in range(idx + 1, len(settings.staged_items)):
            child = settings.staged_items[i]
            if child.indent > my_indent: 
                child.use = new_state
            else: 
                break
        is_updating_hierarchy = False

# Global cache to prevent Blender from garbage-collecting the dynamic enum strings
_catalog_cache = []

def get_asset_catalogs(self, context):
    global _catalog_cache
    _catalog_cache.clear()
    _catalog_cache.append(("", "No Catalog", "Do not assign to a catalog", "FILE_FOLDER", 0))
    
    if context is None:
        return _catalog_cache
        
    seen_ids = set()
    idx = 1
    
    paths_to_check = []
    
    if hasattr(context, "preferences") and context.preferences:
        for lib in context.preferences.filepaths.asset_libraries:
            paths_to_check.append(lib.path)
            
    if bpy.data.filepath:
        paths_to_check.append(os.path.dirname(bpy.data.filepath))
        
    for path in paths_to_check:
        if not path: 
            continue
        cat_file = os.path.join(path, "blender_assets.cats.txt")
        if os.path.exists(cat_file):
            try:
                with open(cat_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith(('#', 'VERSION', '\n')): continue
                        parts = line.strip().split(':')
                        if len(parts) >= 3:
                            cat_id = str(parts[0].strip())
                            cat_path = str(parts[1].strip())
                            if cat_id not in seen_ids:
                                _catalog_cache.append((cat_id, cat_path, "", "ASSET_MANAGER", idx))
                                seen_ids.add(cat_id)
                                idx += 1
            except Exception:
                pass
                
    return _catalog_cache

class StagedAssetItem(PropertyGroup):
    name: StringProperty()
    item_type: EnumProperty(items=[('COLLECTION', "Collection", ""), ('OBJECT', "Object", "")])
    obj_type: StringProperty(default="")
    use: BoolProperty(default=True, description="Include this item", update=update_hierarchy_selection)
    indent: IntProperty(default=0) 

class STAGED_UL_items(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.indent > 0:
            for _ in range(item.indent): 
                row.separator(factor=1.5)
        row.prop(item, "use", text="")
        
        if item.item_type == 'COLLECTION': 
            icon_str = 'OUTLINER_COLLECTION'
        else:
            icon_map = {
                'MESH': 'OUTLINER_OB_MESH', 'LIGHT': 'OUTLINER_OB_LIGHT',
                'CAMERA': 'OUTLINER_OB_CAMERA', 'EMPTY': 'OUTLINER_OB_EMPTY',
                'ARMATURE': 'OUTLINER_OB_ARMATURE', 'CURVE': 'OUTLINER_OB_CURVE'
            }
            icon_str = icon_map.get(item.obj_type, 'OBJECT_DATA')
        row.label(text=item.name, icon=icon_str)

class AppendEverythingSettings(PropertyGroup):
    is_link: BoolProperty(name="Link Data", default=False)
    add_to_scene: BoolProperty(name="Add to Current Scene", description="Uncheck to import in the background (assigns Fake User)", default=True)
    custom_prefix: StringProperty(name="Prefix", description="Optional prefix added to imported items", default="")
    show_settings: BoolProperty(name="Advanced Background Data", default=False)
    
    staged_items: CollectionProperty(type=StagedAssetItem)
    staged_active_index: IntProperty()
    target_filepath: StringProperty(subtype='FILE_PATH')
    pending_import: BoolProperty(default=False, options={'HIDDEN'})
    pending_transaction_id: StringProperty(default="", options={'HIDDEN'})

    import_materials: BoolProperty(name="Materials", default=True)
    import_textures: BoolProperty(name="Textures & Images", default=True)
    import_node_groups: BoolProperty(name="Node Groups", default=True)
    import_worlds: BoolProperty(name="Worlds (Environment)", default=True)
    import_texts: BoolProperty(name="Scripts (Texts)", default=True)
    import_workspaces: BoolProperty(name="Workspaces (Tabs)", default=True)
    import_actions: BoolProperty(name="Actions (Animations)", default=True)
    import_others: BoolProperty(name="Everything Else", default=True)
    apply_render_settings: BoolProperty(name="Overwrite Current Render Settings", default=False)

    auto_mark_assets: BoolProperty(name="Auto-Mark as Assets", default=False)
    mark_collections: BoolProperty(name="Mark Collections", default=True)
    mark_objects: BoolProperty(name="Mark Objects", default=True)
    mark_materials: BoolProperty(name="Mark Materials", default=False)
    
    asset_catalog_enum: EnumProperty(
        name="Catalog", 
        description="Select a catalog to place marked assets into", 
        items=get_asset_catalogs
    )

def extract_zip_to_cache(filepath):
    cache_dir = os.path.join(bpy.app.tempdir, "universal_appender_cache")
    if os.path.exists(cache_dir): 
        shutil.rmtree(cache_dir, ignore_errors=True)
    os.makedirs(cache_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(cache_dir)
        for root, _, files in os.walk(cache_dir):
            for file in files:
                if file.endswith(".blend"): 
                    return os.path.join(root, file)
    except Exception:
        pass
    return None

def deduplicate_materials(appended_objects):
    for obj in appended_objects:
        if obj.type == 'MESH': 
            for slot in obj.material_slots:
                mat = slot.material
                if mat and re.search(r'\.\d{3}$', mat.name):
                    base_name = re.sub(r'\.\d{3}$', '', mat.name)
                    if base_name in bpy.data.materials:
                        slot.material = bpy.data.materials[base_name]

TRANSACTION_PROP = "ua_transaction"

def _tag_transaction(item, transaction_id, status=None):
    if item is None: return
    try:
        item[TRANSACTION_PROP] = transaction_id
        if status: item["ua_status"] = status
    except Exception: pass

def _remove_transaction_tags(item):
    for key in (TRANSACTION_PROP, "ua_status"):
        if key in item:
            try: del item[key]
            except Exception: pass

def _transaction_items(transaction_id):
    if not transaction_id: return [], [], []
    objects = [o for o in bpy.data.objects if o.get(TRANSACTION_PROP) == transaction_id]
    collections = [c for c in bpy.data.collections if c.get(TRANSACTION_PROP) == transaction_id]
    materials = [m for m in bpy.data.materials if m.get(TRANSACTION_PROP) == transaction_id]
    return objects, collections, materials

def cleanup_transaction(transaction_id):
    if not transaction_id: return
    objects, collections, materials = _transaction_items(transaction_id)
    for obj in list(objects):
        try: bpy.data.objects.remove(obj, do_unlink=True)
        except Exception: pass
    for coll in list(collections):
        try: bpy.data.collections.remove(coll, do_unlink=True)
        except Exception: pass
    for mat in list(materials):
        try: bpy.data.materials.remove(mat)
        except Exception: pass
    try: bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except Exception: pass

def is_connected(target, users):
    for user in users:
        try:
            if user.parent == target: return True
            for mod in user.modifiers:
                if getattr(mod, 'object', None) == target or getattr(mod, 'target', None) == target: return True
            for const in user.constraints:
                if getattr(const, 'target', None) == target: return True
            if getattr(user, "instance_object", None) == target: return True
            if user.animation_data and user.animation_data.drivers:
                for fcurve in user.animation_data.drivers:
                    try:
                        for var in fcurve.driver.variables:
                            for t in var.targets:
                                if t.id == target: return True
                    except Exception: pass
        except ReferenceError: continue
    return False

def get_dependency_closure(intended_objs, tag_alongs):
    keep = set(intended_objs)
    changed = True
    while changed:
        changed = False
        for candidate in tag_alongs:
            if candidate in keep: continue
            if is_connected(candidate, keep):
                keep.add(candidate)
                changed = True
    return keep

def get_staged_name(obj_name, staged_names):
    if obj_name in staged_names: return obj_name
    parts = obj_name.rsplit('.', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return get_staged_name(parts[0], staged_names)
    return None

def _get_new_objects(existing_ptrs):
    return [obj for obj in bpy.data.objects if obj.as_pointer() not in existing_ptrs]

def _clear_scan_data(scan_objects, scan_collections, scan_scenes):
    for scn in list(scan_scenes):
        try:
            if scn and scn.name in bpy.data.scenes: bpy.data.scenes.remove(scn)
        except Exception: pass
    for coll in list(scan_collections):
        try:
            if coll and coll.name in bpy.data.collections and coll.library: bpy.data.collections.remove(coll, do_unlink=True)
        except Exception: pass
    for obj in list(scan_objects):
        try:
            if obj and obj.name in bpy.data.objects and obj.library: bpy.data.objects.remove(obj, do_unlink=True)
        except Exception: pass

def finalize_import(context, settings, dependency_mode, transaction_id, use_progress=False):
    success = False
    try:
        if use_progress:
            context.window_manager.progress_update(75)

        intended_objs = [obj for obj in bpy.data.objects if obj.get(TRANSACTION_PROP) == transaction_id and obj.get("ua_status") == 'INTENDED']
        tag_alongs = [obj for obj in bpy.data.objects if obj.get(TRANSACTION_PROP) == transaction_id and obj.get("ua_status") == 'TAG_ALONG']
        appended_colls = [coll for coll in bpy.data.collections if coll.get(TRANSACTION_PROP) == transaction_id]
        appended_mats = [mat for mat in bpy.data.materials if mat.get(TRANSACTION_PROP) == transaction_id]

        if dependency_mode == 'STRICT':
            objects_to_delete = list(tag_alongs)
            surviving_objs = list(intended_objs)
        else:
            keep = get_dependency_closure(intended_objs, tag_alongs)
            objects_to_delete = [obj for obj in tag_alongs if obj not in keep]
            surviving_objs = list(keep)

        for obj in objects_to_delete:
            try: bpy.data.objects.remove(obj, do_unlink=True)
            except Exception: pass

        prefix = settings.custom_prefix
        clean_name = os.path.splitext(os.path.basename(settings.target_filepath))[0]
        master_coll_name = f"{prefix}Imported_{clean_name}" if prefix else f"Imported_{clean_name}"

        master_coll = bpy.data.collections.new(name=master_coll_name)
        _tag_transaction(master_coll, transaction_id, "MASTER")
        
        if settings.add_to_scene:
            context.collection.children.link(master_coll)
        else:
            master_coll.use_fake_user = True

        for coll in appended_colls:
            if coll == master_coll: continue
            if not settings.add_to_scene: coll.use_fake_user = True
            try: master_coll.children.link(coll)
            except RuntimeError: pass

        objs_in_colls = {obj for coll in appended_colls for obj in coll.objects if obj.get(TRANSACTION_PROP) == transaction_id}
        for obj in surviving_objs:
            if not settings.add_to_scene: obj.use_fake_user = True
            if obj not in objs_in_colls:
                try: master_coll.objects.link(obj)
                except RuntimeError: pass

        if prefix:
            for obj in surviving_objs:
                if obj.library is None and not obj.name.startswith(prefix):
                    obj.name = prefix + obj.name
            def rename_collections(collections):
                for coll in collections:
                    if coll.library is None and not coll.name.startswith(prefix) and coll != master_coll:
                        coll.name = prefix + coll.name
                    rename_collections(coll.children)
            rename_collections(master_coll.children)

        if not settings.is_link:
            deduplicate_materials(surviving_objs)

        if use_progress:
            context.window_manager.progress_update(90)

        if settings.auto_mark_assets and not settings.is_link:
            for item_list, toggle in [
                (appended_colls, settings.mark_collections),
                (surviving_objs, settings.mark_objects),
                (appended_mats, settings.mark_materials),
            ]:
                if toggle:
                    for item in item_list:
                        if hasattr(item, "asset_mark"):
                            item.asset_mark()
                            if hasattr(item, "asset_generate_preview"):
                                item.asset_generate_preview()
                            if settings.asset_catalog_enum and hasattr(item, "asset_data"):
                                item.asset_data.catalog_id = settings.asset_catalog_enum

        for group in [surviving_objs, appended_colls, appended_mats, [master_coll]]:
            for item in group:
                _remove_transaction_tags(item)

        try: bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        except Exception: pass

        success = True
        return True
    finally:
        if use_progress:
            context.window_manager.progress_update(100)
            context.window_manager.progress_end()
            
        cache_dir = os.path.join(bpy.app.tempdir, "universal_appender_cache")
        if os.path.exists(cache_dir):
            try: shutil.rmtree(cache_dir, ignore_errors=True)
            except Exception: pass
        if success:
            settings.staged_items.clear()
            settings.target_filepath = ""
            settings.pending_import = False
            settings.pending_transaction_id = ""

def stage_import(context, settings, use_progress=False):
    if use_progress:
        context.window_manager.progress_update(20)

    target_colls = [i.name for i in settings.staged_items if i.use and i.item_type == 'COLLECTION']
    target_objs = [i.name for i in settings.staged_items if i.use and i.item_type == 'OBJECT']
    all_staged_obj_names = {i.name for i in settings.staged_items if i.item_type == 'OBJECT'}
    checked_obj_names = {i.name for i in settings.staged_items if i.use and i.item_type == 'OBJECT'}

    if not target_colls and not target_objs: raise ValueError("Nothing is selected to import.")
    filepath = settings.target_filepath
    if not filepath: raise ValueError("No source file is selected.")

    existing_object_ptrs = {obj.as_pointer() for obj in bpy.data.objects}
    transaction_id = uuid.uuid4().hex

    attr_map = {
        'materials': settings.import_materials, 'textures': settings.import_textures,
        'images': settings.import_textures, 'node_groups': settings.import_node_groups,
        'worlds': settings.import_worlds, 'texts': settings.import_texts,
        'workspaces': settings.import_workspaces, 'actions': settings.import_actions,
        'lights': True, 'cameras': True, 'meshes': True, 'curves': True, 'armatures': True
    }

    if use_progress:
        context.window_manager.progress_update(40)

    with bpy.data.libraries.load(filepath, link=settings.is_link) as (data_from, data_to):
        data_to.collections = target_colls
        data_to.objects = target_objs
        for attr in dir(data_from):
            if attr.startswith('_') or attr in {'collections', 'objects', 'scenes'}: continue
            if attr in attr_map:
                if attr_map[attr]: setattr(data_to, attr, getattr(data_from, attr))
            elif settings.import_others: setattr(data_to, attr, getattr(data_from, attr))

    if use_progress:
        context.window_manager.progress_update(60)

    appended_objs = _get_new_objects(existing_object_ptrs)
    appended_colls = [c for c in data_to.collections if c is not None]
    appended_mats = [m for m in getattr(data_to, 'materials', []) if m is not None]

    for obj in appended_objs: _tag_transaction(obj, transaction_id, "PENDING")
    for coll in appended_colls: _tag_transaction(coll, transaction_id, "PENDING")
    for mat in appended_mats: _tag_transaction(mat, transaction_id, "PENDING")

    intended_objs = []
    tag_alongs = []

    for obj in appended_objs:
        orig_name = get_staged_name(obj.name, all_staged_obj_names)
        is_intended = (orig_name is not None and orig_name in checked_obj_names)
        if is_intended:
            _tag_transaction(obj, transaction_id, "INTENDED")
            intended_objs.append(obj)
        else:
            _tag_transaction(obj, transaction_id, "TAG_ALONG")
            tag_alongs.append(obj)

    keep = get_dependency_closure(intended_objs, tag_alongs)
    has_missing_deps = any(obj in keep and obj not in intended_objs for obj in tag_alongs)

    settings.pending_transaction_id = transaction_id
    settings.pending_import = True
    return has_missing_deps, transaction_id

class IMPORT_OT_quick_filter(Operator):
    bl_idname = "import_scene.quick_filter"
    bl_label = "Quick Filter"
    filter_type: StringProperty()

    def execute(self, context):
        global is_updating_hierarchy
        is_updating_hierarchy = True
        try:
            settings = context.scene.append_everything_settings
            for item in settings.staged_items:
                if self.filter_type == 'ALL': item.use = True
                elif self.filter_type == 'NONE': item.use = False
                elif self.filter_type == 'MESH_ONLY':
                    if item.item_type == 'COLLECTION': item.use = False
                    else: item.use = item.obj_type in {'MESH', 'CURVE'}
                elif self.filter_type == 'NO_LIGHTS':
                    if item.item_type == 'OBJECT' and item.obj_type == 'LIGHT': item.use = False
                elif self.filter_type == 'NO_CAMERAS':
                    if item.item_type == 'OBJECT' and item.obj_type == 'CAMERA': item.use = False
        finally:
            is_updating_hierarchy = False
        return {'FINISHED'}

class VIEW3D_MT_append_filters(Menu):
    bl_label = "Filters"
    bl_idname = "VIEW3D_MT_append_filters"

    def draw(self, context):
        layout = self.layout
        layout.operator(IMPORT_OT_quick_filter.bl_idname, text="All").filter_type = 'ALL'
        layout.operator(IMPORT_OT_quick_filter.bl_idname, text="None").filter_type = 'NONE'
        layout.separator()
        layout.operator(IMPORT_OT_quick_filter.bl_idname, text="Meshes Only").filter_type = 'MESH_ONLY'
        layout.operator(IMPORT_OT_quick_filter.bl_idname, text="No Lights").filter_type = 'NO_LIGHTS'
        layout.operator(IMPORT_OT_quick_filter.bl_idname, text="No Cameras").filter_type = 'NO_CAMERAS'

class IMPORT_OT_scan_blend(Operator, ImportHelper):
    bl_idname = "import_scene.scan_blend"
    bl_label = "Select File to Scan"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ""
    filter_glob: StringProperty(default="*.blend;*.zip", options={'HIDDEN'}, maxlen=255)

    def execute(self, context):
        settings = context.scene.append_everything_settings
        if settings.pending_import:
            self.report({'WARNING'}, "Finish or cancel the current pending import first.")
            return {'CANCELLED'}

        settings.staged_items.clear()
        filepath = self.filepath
        if filepath.endswith(".zip"):
            filepath = extract_zip_to_cache(filepath)
            if not filepath:
                self.report({'ERROR'}, "Failed to extract ZIP archive.")
                return {'CANCELLED'}

        settings.target_filepath = filepath
        before_objects = {obj.as_pointer() for obj in bpy.data.objects}
        before_collections = {coll.as_pointer() for coll in bpy.data.collections}
        before_scenes = {scene.as_pointer() for scene in bpy.data.scenes}

        scan_objects, scan_collections, scan_scenes = [], [], []
        try:
            with bpy.data.libraries.load(filepath, link=True) as (data_from, data_to):
                data_to.scenes = data_from.scenes
                data_to.collections = data_from.collections

            scan_scenes = [scn for scn in bpy.data.scenes if scn.as_pointer() not in before_scenes]
            scan_collections = [coll for coll in bpy.data.collections if coll.as_pointer() not in before_collections]
            scan_objects = [obj for obj in bpy.data.objects if obj.as_pointer() not in before_objects]

            added_keys = set()
            def add_item(name, item_type, indent, obj_type=""):
                key = (item_type, name)
                if key in added_keys: return
                added_keys.add(key)
                item = settings.staged_items.add()
                item.name = name
                item.item_type = item_type
                item.indent = indent
                item.obj_type = obj_type

            def traverse(coll, indent):
                add_item(coll.name, 'COLLECTION', indent, 'COLLECTION')
                for child in coll.children: traverse(child, indent + 1)
                for obj in coll.objects: add_item(obj.name, 'OBJECT', indent + 1, obj.type)

            for scn in scan_scenes:
                for coll in scn.collection.children: traverse(coll, 0)
                for obj in scn.collection.objects: add_item(obj.name, 'OBJECT', 0, obj.type)

            for coll in scan_collections:
                if coll.name not in {i.name for i in settings.staged_items if i.item_type == 'COLLECTION'}:
                    traverse(coll, 0)
            return {'FINISHED'}
        except Exception as e:
            settings.staged_items.clear()
            settings.target_filepath = ""
            self.report({'ERROR'}, f"Could not read blend file: {e}")
            return {'CANCELLED'}
        finally:
            _clear_scan_data(scan_objects, scan_collections, scan_scenes)

class IMPORT_OT_execute_staged(Operator):
    bl_idname = "import_scene.execute_staged"
    bl_label = "Confirm Import"
    bl_options = {'REGISTER', 'UNDO'}

    dependency_mode: EnumProperty(
        name="Action",
        items=[
            ('SMART', "Keep needed dependencies", "Keep unchecked objects required directly or indirectly"),
            ('STRICT', "Delete unchecked items", "Delete every unchecked object"),
        ],
        default='SMART',
    )

    def invoke(self, context, event):
        settings = context.scene.append_everything_settings
        self.dependency_mode = 'SMART'
        if settings.pending_import:
            self.report({'WARNING'}, "An import transaction is already waiting.")
            return {'CANCELLED'}
            
        context.window_manager.progress_begin(0, 100)
        context.window_manager.progress_update(10)
            
        try:
            has_missing_deps, _ = stage_import(context, settings, use_progress=True)
        except Exception as e:
            context.window_manager.progress_end()
            self.report({'ERROR'}, f"Import staging failed: {e}")
            return {'CANCELLED'}

        if not has_missing_deps:
            try:
                finalize_import(context, settings, 'SMART', settings.pending_transaction_id, use_progress=True)
            except Exception as e:
                cleanup_transaction(settings.pending_transaction_id)
                settings.pending_import = False
                settings.pending_transaction_id = ""
                context.window_manager.progress_end()
                self.report({'ERROR'}, f"Import failed: {e}")
                return {'CANCELLED'}
            return {'FINISHED'}

        context.window_manager.progress_end() 
        return context.window_manager.invoke_props_dialog(self, width=450, title="Dependencies Detected", confirm_text="Import", cancel_default=True)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.append_everything_settings
        layout.label(text="Some unchecked objects are required by your selected objects.", icon='ERROR')
        layout.separator()
        layout.prop(self, "dependency_mode", expand=True)
        if settings.is_link:
            layout.label(text="This import will link the selected data.", icon='LINK_BLEND')
        else:
            layout.label(text="This import will append the selected data.", icon='DUPLICATE')

    def execute(self, context):
        settings = context.scene.append_everything_settings
        if not settings.pending_import: return {'CANCELLED'}
        
        context.window_manager.progress_begin(0, 100)
        context.window_manager.progress_update(30)
            
        transaction_id = settings.pending_transaction_id
        try:
            finalize_import(context, settings, self.dependency_mode, transaction_id, use_progress=True)
        except Exception as e:
            cleanup_transaction(transaction_id)
            settings.pending_import = False
            settings.pending_transaction_id = ""
            context.window_manager.progress_end()
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def cancel(self, context):
        settings = context.scene.append_everything_settings
        if settings.pending_transaction_id:
            cleanup_transaction(settings.pending_transaction_id)
        settings.pending_import = False
        settings.pending_transaction_id = ""

class VIEW3D_PT_append_everything_panel(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Append'
    bl_label = "Universal Appender"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.append_everything_settings
        
        main_box = layout.box()
        col = main_box.column(align=True)
        col.scale_y = 1.3
        col.operator(IMPORT_OT_scan_blend.bl_idname, text="Select File to Scan...", icon='FILE_FOLDER')
        
        if settings.target_filepath:
            main_box.separator(factor=0.5)
            
            hier_box = main_box.box()
            hier_box.label(text=os.path.basename(settings.target_filepath), icon='FILE_BLEND')
            
            header_row = hier_box.row(align=True)
            header_row.prop(settings, "custom_prefix", icon='SYNTAX_OFF')
            header_row.menu(VIEW3D_MT_append_filters.bl_idname, icon='FILTER', text="")
            
            hier_box.template_list("STAGED_UL_items", "", settings, "staged_items", settings, "staged_active_index", rows=8)
            
            main_box.separator(factor=0.8)
            
            mode_row = main_box.row(align=True)
            mode_row.scale_y = 1.2
            mode_text = "Mode: Link (References File)" if settings.is_link else "Mode: Append (Copies to File)"
            mode_icon = 'LINK_BLEND' if settings.is_link else 'DUPLICATE'
            mode_row.prop(settings, "is_link", toggle=True, text=mode_text, icon=mode_icon)
            
            exec_row = main_box.row(align=True)
            exec_row.scale_y = 1.4
            btn_text = "Confirm Link" if settings.is_link else "Confirm Append"
            exec_row.operator(IMPORT_OT_execute_staged.bl_idname, text=btn_text, icon='IMPORT')
            
            main_box.separator(factor=0.5)
            
            set_row = main_box.row(align=True)
            set_row.scale_y = 1.2
            set_row.prop(settings, "show_settings", toggle=True, text="Advanced Settings", icon='PREFERENCES')
            
            if settings.show_settings:
                s_box = main_box.box()
                s_col = s_box.column(align=True)
                s_col.prop(settings, "add_to_scene", icon='SCENE_DATA')
                s_col.separator(factor=0.5)
                s_col.prop(settings, "import_materials")
                s_col.prop(settings, "import_textures")
                s_col.prop(settings, "import_worlds")
                s_col.prop(settings, "import_node_groups")
                s_col.prop(settings, "import_texts")
                s_col.prop(settings, "import_workspaces")
                s_col.prop(settings, "import_actions")
                s_col.separator(factor=0.5)
                s_col.prop(settings, "import_others")
                s_box.separator(factor=0.5)
                s_box.prop(settings, "apply_render_settings")
            
            if not settings.is_link:
                main_box.separator(factor=0.5)
                asset_box = main_box.box()
                asset_box.prop(settings, "auto_mark_assets", icon='ASSET_MANAGER')
                if settings.auto_mark_assets:
                    acol = asset_box.column(align=True)
                    # Notice/Tip for catalogs requiring a saved blend file
                    acol.label(text="Tip: Save file (Ctrl+S) to populate catalogs", icon='INFO')
                    acol.separator(factor=0.3)
                    acol.prop(settings, "mark_collections")
                    acol.prop(settings, "mark_objects")
                    acol.prop(settings, "mark_materials")
                    acol.separator(factor=0.5)
                    acol.prop(settings, "asset_catalog_enum", text="")

classes = (
    StagedAssetItem, STAGED_UL_items, AppendEverythingSettings,
    VIEW3D_MT_append_filters, IMPORT_OT_quick_filter, 
    IMPORT_OT_scan_blend, IMPORT_OT_execute_staged, 
    VIEW3D_PT_append_everything_panel,
)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.append_everything_settings = PointerProperty(type=AppendEverythingSettings)

def unregister():
    del bpy.types.Scene.append_everything_settings
    for cls in reversed(classes): bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()