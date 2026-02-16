bl_info = {
    "name": "Standalone Actions",
    "author": "Pr08, Codex",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Animation > Standalone Actions",
    "description": "Unlink all Action users so actions become independent datablocks",
    "category": "Animation",
}

import bpy


def iter_anim_owners():
    seen = set()
    for prop in bpy.data.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type != "COLLECTION":
            continue
        try:
            collection = getattr(bpy.data, prop.identifier)
        except Exception:
            continue
        try:
            iterator = iter(collection)
        except TypeError:
            continue
        for item in iterator:
            if not hasattr(item, "as_pointer"):
                continue
            ptr = item.as_pointer()
            if ptr in seen:
                continue
            seen.add(ptr)
            if hasattr(item, "animation_data"):
                yield item


def clear_action_slots(anim_data):
    if not anim_data:
        return
    if hasattr(anim_data, "action"):
        anim_data.action = None
    if hasattr(anim_data, "action_tweak_storage"):
        anim_data.action_tweak_storage = None
    if hasattr(anim_data, "nla_tracks"):
        while anim_data.nla_tracks:
            anim_data.nla_tracks.remove(anim_data.nla_tracks[0])


def clear_pointer_action_props(target_ids, depth_limit=3):
    visited = set()

    def recurse(struct, depth):
        if struct is None or depth < 0 or not hasattr(struct, "bl_rna"):
            return
        key = struct.as_pointer() if hasattr(struct, "as_pointer") else id(struct)
        if key in visited:
            return
        visited.add(key)

        for prop in struct.bl_rna.properties:
            pid = prop.identifier
            if pid == "rna_type":
                continue
            ptype = prop.type
            fixed = getattr(prop, "fixed_type", None)

            if ptype == "POINTER" and fixed and fixed.identifier == "Action" and not prop.is_readonly:
                try:
                    if getattr(struct, pid) is not None:
                        setattr(struct, pid, None)
                except Exception:
                    pass
                continue

            if depth == 0:
                continue

            if ptype == "COLLECTION":
                try:
                    coll = getattr(struct, pid)
                except Exception:
                    continue
                for item in coll:
                    if hasattr(item, "bl_rna"):
                        recurse(item, depth - 1)

    for target in target_ids:
        recurse(target, depth_limit)


def run_unlink():
    original_names = [action.name for action in bpy.data.actions]

    for owner in iter_anim_owners():
        clear_action_slots(owner.animation_data)

    for _ in range(5):
        user_map = bpy.data.user_map()
        unresolved_ids = set()
        total_real_users = 0
        for action in bpy.data.actions:
            users = user_map.get(action, ())
            count = len(users)
            total_real_users += count
            unresolved_ids.update(users)
        if total_real_users == 0 or not unresolved_ids:
            break
        clear_pointer_action_props(unresolved_ids, depth_limit=3)

    final_user_map = bpy.data.user_map()
    remaining_real = {
        action.name: len(final_user_map.get(action, ()))
        for action in bpy.data.actions
        if len(final_user_map.get(action, ())) > 0
    }

    orphan_non_fake = [
        action.name
        for action in bpy.data.actions
        if len(final_user_map.get(action, ())) == 0 and not action.use_fake_user
    ]

    final_names = {action.name for action in bpy.data.actions}
    original_name_set = set(original_names)
    unexpected_new_actions = sorted(final_names - original_name_set)

    return {
        "total_actions": len(bpy.data.actions),
        "remaining_real": remaining_real,
        "orphan_non_fake": sorted(orphan_non_fake),
        "unexpected_new_actions": unexpected_new_actions,
    }


def purge_orphan_non_fake_actions():
    final_user_map = bpy.data.user_map()
    to_remove = [
        action
        for action in list(bpy.data.actions)
        if len(final_user_map.get(action, ())) == 0 and not action.use_fake_user
    ]
    removed = []
    for action in to_remove:
        removed.append(action.name)
        bpy.data.actions.remove(action)
    return sorted(removed)


class ANIM_OT_make_actions_standalone(bpy.types.Operator):
    bl_idname = "anim.make_actions_standalone"
    bl_label = "Make Actions Standalone + Purge"
    bl_description = "Unlink all Action users and purge orphan actions with Fake User disabled"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        stats = run_unlink()
        purged = purge_orphan_non_fake_actions()
        stats["total_actions"] = len(bpy.data.actions)
        stats["orphan_non_fake"] = [name for name in stats["orphan_non_fake"] if name not in purged]

        if stats["unexpected_new_actions"]:
            self.report({"WARNING"}, f"Unexpected new actions: {stats['unexpected_new_actions']}")
            return {"FINISHED"}

        if stats["remaining_real"]:
            self.report({"WARNING"}, f"Some actions still have users: {len(stats['remaining_real'])}")
            return {"FINISHED"}

        msg = f"Done. Actions: {stats['total_actions']}. Purged: {len(purged)}."
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class ANIM_PT_standalone_actions(bpy.types.Panel):
    bl_label = "Standalone Actions"
    bl_idname = "ANIM_PT_standalone_actions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Animation"

    def draw(self, context):
        layout = self.layout
        layout.operator("anim.make_actions_standalone", icon="UNLINKED")


classes = (
    ANIM_OT_make_actions_standalone,
    ANIM_PT_standalone_actions,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except RuntimeError:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
