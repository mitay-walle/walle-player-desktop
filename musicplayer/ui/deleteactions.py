"""«На удаление» in the desktop app: the delete buttons and Del put tracks on the list (core/deletemarks.py) instead
of deleting them; the files go to the bin only from the list itself (ui/deletemarks_dialog.py), as on the phones."""

from __future__ import annotations

from musicplayer.core import deletemarks

LABEL, UNMARK = "На удаление", "Снять пометку"
TIP = "В список «На удаление» (внизу слева): удалить файлы можно оттуда. Ещё раз — снять пометку"


def is_marked(ctx, tracks: list[dict]) -> bool:
    """Every one of the tracks is on the list (by the listener the app acts for)."""
    if not tracks:
        return False
    deletemarks.ensure(ctx.db)
    mine = {key for kind, key in ctx.db.conn.execute(
        "SELECT kind, key FROM delete_marks WHERE kind='track' AND user=?", (ctx.listener,))}
    return all(str(t["id"]) in mine for t in tracks)


def label(ctx, tracks: list[dict]) -> str:
    return UNMARK if is_marked(ctx, tracks) else LABEL


def toggle(ctx, parent, tracks: list[dict], on: bool | None = None) -> bool:
    """Marks the tracks (or takes the mark off when all are marked already) and offers «Отменить».  Returns the new
    state."""
    if not tracks:
        return False
    on = not is_marked(ctx, tracks) if on is None else on

    def apply(state: bool) -> None:
        for t in tracks:
            deletemarks.set_mark(ctx.db, ctx.listener, "track", str(t["id"]), state)
        ctx.events.delete_marks_changed.emit()
    apply(on)
    what = f"«{tracks[0]['title']}»" if len(tracks) == 1 else f"треков: {len(tracks)}"
    win = parent.window() if parent is not None else None
    if not hasattr(win, "offer_undo"):                     # the mini player is a window of its own
        from PySide6.QtWidgets import QApplication
        win = next((w for w in QApplication.topLevelWidgets()
                    if hasattr(w, "offer_undo") and getattr(w, "ctx", None) is ctx), None)
    if win is not None:
        win.offer_undo(f"На удаление: {what}" if on else f"Пометка снята: {what}", lambda: apply(not on))
    return on
