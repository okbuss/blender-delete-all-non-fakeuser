import bpy
import traceback

# =========================
# SETTINGS (CTRL+F these)
# =========================
DRY_RUN = False                 # True = log only
KEEP_FAKE_USER = True           # Fake User = keep
FORCE_DELETE_EVEN_IF_USERS_GT0 = True

SKIP_LINKED = True              # skip library-linked datablocks
SKIP_ASSETS = True              # skip asset-marked actions
RUN_ORPHANS_PURGE = True

LOG_TEXT_NAME = "Action_Clean_Log"


# -------------------------
# Logging to a Text datablock
# -------------------------
log_text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(LOG_TEXT_NAME)
log_text.clear()

def log(msg: str):
    print(msg)
    log_text.write(msg + "\n")

def popup(title="Action cleanup", icon='INFO'):
    def draw(self, context):
        self.layout.label(text=f"See Text: {LOG_TEXT_NAME}")
    try:
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    except Exception:
        pass

def can_edit_id(id_block) -> bool:
    return not (SKIP_LINKED and getattr(id_block, "library", None))

def is_candidate(act: bpy.types.Action) -> bool:
    if KEEP_FAKE_USER and act.use_fake_user:
        return False
    if SKIP_LINKED and act.library:
        return False
    if SKIP_ASSETS and getattr(act, "asset_data", None):
        return False
    return True

def fmt_id(idb):
    try:
        return f"{type(idb).__name__}('{idb.name}')"
    except Exception:
        return f"{type(idb).__name__}"

def clear_editors_referencing(actions_set):
    """Clear Dope Sheet / Graph / NLA editor action pointers across all screens."""
    cleared = 0
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                # direct pointer
                if hasattr(space, "action") and getattr(space, "action", None) in actions_set:
                    a = space.action
                    log(f"[EDITOR] Clear space.action '{a.name}' in Screen='{screen.name}' Area='{area.type}' Space='{space.type}'")
                    if not DRY_RUN:
                        space.action = None
                    cleared += 1

                # dopesheet pointer
                ds = getattr(space, "dopesheet", None)
                if ds and hasattr(ds, "action") and getattr(ds, "action", None) in actions_set:
                    a = ds.action
                    log(f"[EDITOR] Clear dopesheet.action '{a.name}' in Screen='{screen.name}' Area='{area.type}' Space='{space.type}'")
                    if not DRY_RUN:
                        ds.action = None
                    cleared += 1
    return cleared

def unlink_action_from_animdata(owner_label, ad, act):
    """Unlink action from animation_data.action and remove any NLA strips that reference it."""
    changed = 0
    if not ad:
        return 0

    # Active action slot
    if getattr(ad, "action", None) == act:
        log(f"[ANIMDATA] {owner_label}: clear animation_data.action '{act.name}'")
        if not DRY_RUN:
            ad.action = None
        changed += 1

    # NLA strips
    try:
        for track in list(ad.nla_tracks):
            for strip in list(track.strips):
                if getattr(strip, "action", None) == act:
                    log(f"[NLA] {owner_label}: remove strip '{strip.name}' (track '{track.name}') using '{act.name}'")
                    if not DRY_RUN:
                        track.strips.remove(strip)
                    changed += 1
            # optionally remove empty tracks
            if not DRY_RUN and len(track.strips) == 0:
                # Only remove if it exists and is truly empty
                try:
                    ad.nla_tracks.remove(track)
                    log(f"[NLA] {owner_label}: removed empty track '{track.name}'")
                    changed += 1
                except Exception:
                    pass
    except Exception:
        pass

    return changed

def try_orphans_purge():
    try:
        bpy.ops.outliner.orphans_purge(do_recursive=True)
        return True
    except Exception as e:
        log(f"[WARN] orphans_purge failed (context issue is common): {e}")
        return False


try:
    log("=== Nuke non-Fake-User Actions (Fake User = keep) ===")
    log(f"DRY_RUN={DRY_RUN}  FORCE_DELETE_EVEN_IF_USERS_GT0={FORCE_DELETE_EVEN_IF_USERS_GT0}")
    log(f"Total actions in file: {len(bpy.data.actions)}")

    # Work by name so we never hold dead Action pointers
    candidate_names = [a.name for a in bpy.data.actions if is_candidate(a)]
    log(f"Candidates: {len(candidate_names)}")

    if not candidate_names:
        log("Nothing to do.")
        popup(title="No actions matched", icon='INFO')
        raise SystemExit

    # For clearing editor refs, get current Action objects (safe at this point)
    candidates_now = [bpy.data.actions.get(n) for n in candidate_names]
    candidates_now = [a for a in candidates_now if a is not None]
    cleared_ui = clear_editors_referencing(set(candidates_now))
    log(f"[STEP] Cleared editor refs: {cleared_ui}")

    removed = 0
    failed = 0
    total_unlinked = 0

    # Recompute user_map per action (more accurate after each deletion)
    for name in list(candidate_names):
        act = bpy.data.actions.get(name)
        if act is None:
            continue  # already removed

        # store anything we might print BEFORE deletion
        act_name = act.name
        act_users_before = act.users
        act_fake = act.use_fake_user

        umap = bpy.data.user_map()
        users = list(umap.get(act, []))

        log(f"\n--- '{act_name}' users={act_users_before} fake_user={act_fake} ---")
        if users:
            for u in users:
                log(f"  uses: {fmt_id(u)}")
        else:
            log("  uses: (none listed by user_map)")

        # Step A: explicitly unlink from the users Blender reports
        for u in users:
            if not isinstance(u, bpy.types.ID):
                continue
            if not can_edit_id(u):
                continue

            ad = getattr(u, "animation_data", None)
            if ad:
                total_unlinked += unlink_action_from_animdata(fmt_id(u), ad, act)

            # Common: objects also have data + shapekeys animdata
            if isinstance(u, bpy.types.Object):
                data = getattr(u, "data", None)
                if data and can_edit_id(data):
                    ad2 = getattr(data, "animation_data", None)
                    if ad2:
                        total_unlinked += unlink_action_from_animdata(f"{fmt_id(u)}.data", ad2, act)

                    sk = getattr(data, "shape_keys", None)
                    if sk and can_edit_id(sk):
                        ad3 = getattr(sk, "animation_data", None)
                        if ad3:
                            total_unlinked += unlink_action_from_animdata(f"{fmt_id(u)}.shape_keys", ad3, act)

        # Step B: remove the action datablock
        if DRY_RUN:
            continue

        act = bpy.data.actions.get(act_name)
        if act is None:
            continue

        try:
            if (not FORCE_DELETE_EVEN_IF_USERS_GT0) and act.users > 0:
                log(f"[SKIP] '{act_name}' still has users={act.users}")
                continue

            bpy.data.actions.remove(act, do_unlink=True, do_id_user=True, do_ui_user=True)
            removed += 1
            log(f"[OK] Removed '{act_name}'")
        except ReferenceError:
            # This can happen if Blender removed it during do_unlink; treat as success-ish
            removed += 1
            log(f"[OK] Removed '{act_name}' (ReferenceError after removal is normal)")
        except Exception as e:
            failed += 1
            log(f"[FAIL] Could not remove '{act_name}': {e}")

    log("\n=== DONE ===")
    log(f"Unlinked refs: {total_unlinked}")
    log(f"Removed: {removed}  Failed: {failed}")

    if RUN_ORPHANS_PURGE and not DRY_RUN:
        ok = try_orphans_purge()
        log(f"[STEP] orphans_purge attempted: {ok}")

    log("\nIf the Action dropdown still shows old names: save + reopen (UI list can lag).")
    popup(title="Action cleanup finished", icon='INFO')

except SystemExit:
    pass
except Exception:
    log("\n!!! SCRIPT ERROR !!!")
    log(traceback.format_exc())
    popup(title="Action cleanup failed", icon='ERROR')
