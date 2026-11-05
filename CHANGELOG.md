# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The Windows app, the library server and the Android app ([WallePlayerMobile](https://www.rustore.ru/catalog/app/com.mitaywalle.player))
share one version number. A patch release for one platform takes the next free number, so some numbers exist for one
platform only. Entries are prefixed with the platform: **Desktop** (Windows app), **Server** (library server, also built
into the desktop app), **Web** (web player), **Android**.

## [Unreleased]

## [0.4.72] - 2026-11-06

### Changed

- Desktop: the delete buttons (library 🗑 and Del, labels panel, label popup, mini player, labelling feed) now put the
  track on the «На удаление» list, as on the phones; pressing again takes the mark off. Files are deleted only from that
  list.
- Server: My Wave repeats less. A played track's chance comes back gradually: 2% right after, ~30% after a day, ~60%
  after three days, ~90% after a week (was a flat 2-hour cooldown).
- Android: a new or changed wave sends everything already played in the queue to be excluded; when a narrow selection
  has all been played, the wave starts another round instead of stopping.
- Android: the phone remembers what it started playing during the last 7 days and sends it to the wave (`heard`), so
  skipped and half-heard tracks also come back rarely, guests included.

### Fixed

- Android: the same tracks replayed after a restart or a library switch. Each library now keeps its own queue, saved
  and reloaded on a switch; a wave tail older than 3 hours is dropped.
- Android: a share link opens in the current library when it belongs to it, even with two keys (owner and guest) to one
  computer; it used to switch silently to the last-added profile with that address.

## [0.4.71] - 2026-11-05

### Fixed

- Android: Android Auto went silent after a track. The car loaded cover URLs without the library key, got 401 and the
  server banned the phone's address for an hour. Cover URLs now carry the key.
- Android: on 429, 401 or 502–504 the player no longer skips through the queue with errors; it falls back to downloads
  or waits.

### Changed

- Android: a ban by the server is shown plainly, with the time it ends and where to lift it.

## [0.4.70] - 2026-11-04

### Changed

- Desktop: «Other devices» is more compact: everything sits right of the QR code, flags in one row, icon buttons placed
  next to what they act on, no «Close» button, «Refresh» and «Change key» as icons, the QR exactly as tall as the column.
- Tests run in parallel (`pytest -n auto`): about 2.5 minutes instead of 12.
- Android: RuStore release with the changes of 0.4.59–0.4.69; the release-signed APK is attached to the GitHub release.

## [0.4.60–0.4.69] - 2026-11-03 – 2026-11-04

### Added

- Desktop, Server: with the library on a server, «Other devices» manages the server's listeners (QR and link of each,
  add, rename, remove, rights, change key, who is online) and imports likes for them (`/api/listeners`, MCP
  `library_server(action="listeners")`).
- Desktop, Server, Android: artist photos from the Deezer catalogue (only the name is sent), else the biggest album's
  cover; tiles view (`/api/artist_image`).
- Desktop: «Share» in the player bar, on the artists page and in the album menu.
- Android: Android Auto: Wave, Favourites, Playlists, Downloads and voice search on the car screen, offline playback.

### Changed

- Desktop: «Other devices» is one window with «Server» and «This computer» tabs, icon buttons with tooltips, the
  library location in the title, no long texts.
- Server, Android: share links are `<address>/open/track/5` and open straight in the app (App Link).
- Desktop, Web, Android: «feat. B» / «(with B)» counts the track for guest B too; duets in the artist tag are split
  when one of the artists exists on their own.
- Server: «Unfamiliar» skips favourite artists and albums.
- Android: 502–504 count as offline; side swipes switch sub-tabs; label sheet colours; the app reopens on the last tab.

### Fixed

- Desktop: `swap_build.sh` closes leftover background processes that kept files locked.
- Android: sheets close with a swipe down even when their content scrolls.

## [0.4.50–0.4.59] - 2026-10-16 – 2026-11-03

### Added

- Server: headless Linux server (`python -m musicplayer.server --service`, systemd package from
  `scripts/build_linux_server.py`): serves the library, VPS tunnel, rescans, moods for new tracks, daily backups.
- Desktop: works with a library on the home server: a catalogue copy synced both ways every 30 s, tracks missing here
  stream from the server, an optional full mirror of the music (new files only), a «Server» button with sync state,
  pause and a speed limit.
- Desktop, Web, Server: radio by track and by artist; the wave narrowed by lyrics language and voice.
- Desktop: «Duplicates…» for one artist spelled differently; nightly sound-quality check with a «Bad sound (N)» list.
- Desktop, Web: «Share» links without a key; whole artists can be marked for deletion.
- Android: four bottom tabs, artist sorting, radio, hide / mark for deletion for a track, album or artist, download
  progress, share links.

### Changed

- Desktop: all audio analysis runs on the PC (missing tracks are fetched into a temporary cache).
- Desktop: roomier mini player with a volume slider; GitHub Actions release workflow.
- Changing the wave keeps the current track (web, Android); counts exclude what the listener hid.

### Fixed

- Desktop: a moved library keeps its tracks with labels, likes and plays; the home server is found directly on the home
  network.

## [0.4.40–0.4.49] - 2026-10-11 – 2026-10-14

### Added

- Desktop, Web, Server: listeners mark tracks or albums for deletion (`/api/delete-marks`); the desktop list shows who
  and when, deletes or keeps, exports to JSON.
- Desktop: likes and «Hide» on behalf of a chosen listener; a labels drop-down for the track, album or artist;
  labelling the playing track on My Wave; links to artist and album in the player bar; like, labels and delete in the
  mini player.
- Android: «Mark for deletion»; language, voice, recording type and era labels.

### Changed

- Desktop: Favourites sorted by date added; denser mini player.

### Removed

- Desktop: the compact view (Ctrl+K).

### Fixed

- Desktop: hidden genres leave that listener's wave chips; re-importing likes fixes their dates.
- Android: swipe-down closes the full player again.

## [0.4.30–0.4.39] - 2026-10-11

### Added

- Desktop, Web, Android: a «Hide» button next to the like; one «tag» icon for labelling.

### Changed

- Desktop, Web, Server, Android: an English genre tree (Rock, Punk, Metal, Electronic, Hip-Hop, Pop, Jazz, Folk,
  Soundtrack, Oriental); Russian names stay synonyms; combined tags split, duplicates merged (62 names → 42); genres
  are entered in English only.

## [0.4.20–0.4.29] - 2026-10-10 – 2026-10-11

### Added

- Desktop, Web, Server, Android: «Hide» for a track, album, artist, genre or label, per listener, with undo and a
  «Hidden» list (`/api/hidden`).
- Desktop, Web, Android: custom genres as «Family/Sub-genre» paths with autocompletion.
- Desktop: like import from text, CSV/TSV, JSON or M3U with fuzzy matching (MCP `import_likes`); `--quit` closes the app
  cleanly.

### Changed

- Desktop: real scan progress; vector icons in the mini player; each window mode remembers its position.

### Fixed

- Desktop: deleting keeps the shown list and selects the next track; deleted files leave the queue; broken «������»
  titles restored from MusicBrainz; .m4a analysed through ffmpeg; one data folder however the app is started.

## [0.4.10–0.4.19] - 2026-10-09 – 2026-10-10

### Added

- Server: internet access over https through your own VPS (Caddy, sslip.io name).
- Desktop: genre choice on My Wave by family; the Ska-Punk sub-genre.

### Changed

- Desktop, Web, Server: one genre dictionary, a sub-genre always carries its family.
- Web: a desktop layout on the PC.
- Server: access requests only from the home network unless allowed.

### Fixed

- Desktop: «Delete files?» answers Enter / Escape; «Retrain» with an existing model; the VPS tunnel no longer outlives
  the app.

## [0.4.0–0.4.9] - 2026-10-08 – 2026-10-09

### Added

- Desktop: Windows installer (no admin rights, keeps the library data), signed with a self-signed certificate.
- Server: internet access through the router (UPnP), your own VPS or your own address; IPv6; all addresses in
  `/api/info`; per-listener plays for the wave.
- Web: works offline from downloads.
- Android: connects outside home without a new QR; offline mode no longer drops to the connection screen.

### Changed

- Desktop: scanning, training and labelling run in a separate process; desktop and Android versions aligned at 0.4.0.

### Security

- Server: brute-force protection: 10 wrong keys in 10 minutes ban the address for an hour, growing to a week.

## [0.3.0] - 2026-10-08

### Added

- Server, Web: the library on other devices: a web player with My Wave, library, artists, albums, playlists and likes;
  per-listener QR codes, likes and history; access requests by name; labelling from the phone; downloads and offline
  playback; tray mode and start with Windows.

## [0.2.0]

### Added

- Desktop: catalogue and player, mood model (descriptors, CLAP, honest validation), My Wave, labelling feed, MCP server.

[Unreleased]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.72...HEAD
[0.4.72]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.70...v0.4.72
[0.4.70]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.51...v0.4.70
