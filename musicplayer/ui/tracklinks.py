"""«Исполнитель — Альбом» as links: a click opens that artist's / album's tracks in the library."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt

from musicplayer.ui.theme import TOKENS


def html(t: dict | None, color: str | None = None) -> str:
    """Rich text for a QLabel: the artist and the album are links (no underline, the label's own colour)."""
    if not t:
        return ""
    col = color or TOKENS["text_muted"]
    style = f"color:{col}; text-decoration:none;"
    artist = t.get("albumartist") or t.get("artist") or ""
    shown = escape(t.get("artist") or artist)
    feat = f" feat. {escape(', '.join(t['feat']))}" if t.get("feat") else ""
    album = (f'<a href="album" style="{style}">{escape(t["album"])}</a>' if t.get("album")
             else escape("без альбома"))
    return f'<a href="artist" style="{style}">{shown}</a>{feat} — {album}'


def make_link_label(label, ctx, current) -> None:
    """``current()`` gives the track shown in the label; the links open its artist / album in the library."""
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setOpenExternalLinks(False)
    label.setToolTip("Клик по исполнителю или альбому — их треки в библиотеке")
    label.linkActivated.connect(lambda href: open_link(ctx, current(), href))


def open_link(ctx, t: dict | None, href: str) -> None:
    if not t:
        return
    from musicplayer.ui.tagpopup import group_tracks
    album, by_artist = group_tracks(ctx.db, t)
    if href == "album" and album:
        ctx.events.show_rows.emit(album, f"Альбом «{t.get('album')}»")
    elif by_artist:
        ctx.events.show_rows.emit(by_artist, t.get("albumartist") or t.get("artist") or "")
    else:
        return
    ctx.events.navigate.emit("library")
