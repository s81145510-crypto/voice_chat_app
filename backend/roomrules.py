# -*- coding: utf-8 -*-
"""Who may do what inside a room. Pure functions (no database), so they can be tested.

Levels: platform admin 4, moderator 3, room host 2, room admin 1, everyone else 0.
"""
ADMIN, MOD, HOST, ROOM_ADMIN, USER = 4, 3, 2, 1, 0
FOREVER = 2 ** 31 - 1
KICK_SECONDS = 3600
MUTE_MINUTES = (10, 60, 1440, 0)        # 0 means until someone lifts it
ROOM_ADMIN_MAX_MINUTES = 1440
ACTIONS = ('mute', 'unmute', 'kick', 'ban', 'unban', 'admin', 'unadmin')


def level(is_admin, is_mod, is_host, is_room_admin):
    if is_admin: return ADMIN
    if is_mod: return MOD
    if is_host: return HOST
    if is_room_admin: return ROOM_ADMIN
    return USER


def entry_problem(lvl, banned, locked, has_pass):
    """None = may enter; 'banned' or 'locked' = may not."""
    if lvl >= HOST: return None
    if banned: return 'banned'
    if lvl >= ROOM_ADMIN: return None
    if locked and not has_pass: return 'locked'
    return None


def action_problem(action, actor, target, target_is_host, set_by=0, minutes=10, owner=False):
    """Reason why `actor` (a level) may not do `action` to `target` (a level), or None.

    `set_by` is the level of whoever set the restriction that is active now (0 = none):
    nobody may lift or replace a decision made by someone above them.
    """
    if action not in ACTIONS: return 'Unknown action'
    if actor < ROOM_ADMIN: return 'You are not an admin of this room'
    if target_is_host: return 'Cannot act on the room owner'
    if action in ('admin', 'unadmin'):
        if not (owner or actor in (HOST, ADMIN)): return 'Only the room owner can choose room admins'
        if action == 'admin' and target >= MOD: return 'Platform staff do not need to be room admins'
        return None
    if actor <= target: return 'You cannot do this to that person'
    if set_by > actor: return 'Someone above you made this decision'
    if action in ('ban', 'unban') and actor < HOST: return 'Only the room owner can do this'
    if action == 'mute':
        if minutes not in MUTE_MINUTES: return 'Wrong mute time'
        if actor < HOST and (minutes == 0 or minutes > ROOM_ADMIN_MAX_MINUTES): return 'Only the room owner can mute that long'
    return None


def seat_problem(action, actor, target, target_is_host):
    """The mute / unmute / remove buttons on seats and live guests."""
    problem = action_problem('mute', actor, target, target_is_host)
    if problem: return problem
    if action == 'unmute' and actor < HOST: return 'Only the room owner can unmute a seat'
    return None


def until(now, minutes):
    return FOREVER if minutes == 0 else now + minutes * 60
