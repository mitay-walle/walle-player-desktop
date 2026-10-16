"""«Не показывать» in the desktop app: menu items for tracks / albums / artists, a right click on genre and label
chips, and the «Отменить» offer right after hiding (accidental clicks).  The list itself: ui/hiddenlist.py."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from musicplayer.core import hidden

OWNER = hidden.OWNER


def hide(ctx, parent, items: list[tuple[str, str, str]]) -> None:
    """items: (kind, key, label for the message).  Hides them for the owner and offers «Отменить»."""
    items = [(k, key, label) for k, key, label in items if str(key).strip()]
    if not items:
        return
    for kind, key, _ in items:
        hidden.set_hidden(ctx.db, ctx.listener, kind, key)
    ctx.events.hidden_changed.emit()

    def undo() -> None:
        for kind, key, _ in items:
            hidden.set_hidden(ctx.db, ctx.listener, kind, key, False)
        ctx.events.hidden_changed.emit()
    text = (f"Не показывается: {items[0][2]}" if len(items) == 1 else f"Не показываются: {len(items)}") + \
        " — вернуть можно в «Избранное» → «Скрытое»"
    win = parent.window() if parent is not None else None
    if win is not None and hasattr(win, "offer_undo"):
        win.offer_undo(text, undo)


def track_choices(ctx, parent, tracks: list[dict]) -> list[tuple[str, object]]:
    """(text, action) for «Не показывать трек / альбом / исполнителя» (album / artist when the tracks share one)."""
    if not tracks:
        return []
    one = tracks[0]
    out = [(f"трек «{one['title']}»" if len(tracks) == 1 else f"треки ({len(tracks)})",
            lambda: hide(ctx, parent, [("track", str(t["id"]), f"«{t['title']}»") for t in tracks]))]
    artist = one.get("albumartist") or one.get("artist") or ""
    albums = {((t.get("albumartist") or t.get("artist") or ""), t.get("album") or "") for t in tracks}
    if len(albums) == 1 and one.get("album"):
        out.append((f"альбом «{one['album']}»",
                    lambda: hide(ctx, parent, [("album", hidden.album_key(artist, one["album"]), f"альбом «{one['album']}»")])))
    if len({(t.get("albumartist") or t.get("artist") or "") for t in tracks}) == 1 and artist:
        out.append((f"исполнителя «{artist}»", lambda: hide(ctx, parent, [("artist", artist, f"исполнитель «{artist}»")])))
    return out


def add_track_items(menu: QMenu, ctx, parent, tracks: list[dict]) -> None:
    """A «Не показывать» sub-menu in a track's menu."""
    choices = track_choices(ctx, parent, tracks)
    if choices:
        sub = menu.addMenu("Не показывать")
        for text, fn in choices:
            sub.addAction(text, fn)


def show_menu(menu: QMenu, pos) -> None:
    """Modal popup (a separate function: tests answer it without a real mouse)."""
    menu.exec(pos)


def current_track_menu(ctx, button) -> None:
    """The «Не показывать» button next to the like (wave, player bar): the same choices as everywhere; what gets
    hidden stops playing (on to the next track)."""
    t = ctx.player.current
    if t is None:
        return
    m = QMenu(button)
    m.addSection("Не показывать")
    for text, fn in track_choices(ctx, button, [t]):
        m.addAction(text, fn)
    show_menu(m, button.mapToGlobal(button.rect().bottomLeft()))
    if ctx.player.current is t and hidden_now(ctx, t):
        ctx.player.next()


def hidden_now(ctx, t: dict) -> bool:
    return hidden.matcher(ctx.db, ctx.listener)(t)


def chip_menu(button, ctx, kind: str, name: str) -> None:
    """Right click on a genre / label chip: «Не показывать «…»»."""
    button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(pos) -> None:
        m = QMenu(button)
        what = "жанр" if kind == "genre" else "метку"
        m.addAction(f"Не показывать {what} «{name}»",
                    lambda: hide(ctx, button, [(kind, name, f"{what} «{name}»")]))
        m.exec(button.mapToGlobal(pos))
    try:
        button.customContextMenuRequested.disconnect()
    except (RuntimeError, TypeError):
        pass
    button.customContextMenuRequested.connect(show)
