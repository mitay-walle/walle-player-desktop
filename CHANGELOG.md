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

## [0.4.69] - 2026-11-04

### Changed

- Android: the app reopens on the last tab and sub-tab instead of the wave.

## [0.4.68] - 2026-11-04

### Changed

- Desktop: «Other devices» title says where the library is (this computer or the server); «Server» and «This computer»
  are tabs of one window; icon buttons with tooltips, no headers or long explanations; «Online: …» lists every
  connected listener.

## [0.4.67] - 2026-11-04

### Added

- Desktop, Server: like import for a listener of the library server (`POST /api/listeners/{id}/likes`).

## [0.4.66] - 2026-11-04

### Added

- Desktop: with the library on a server, «Other devices» manages the server's listeners: QR and link of each (internet
  address first), copy link, add, rename, remove, labelling rights, change key, who is connected.
- Server: `GET/POST /api/listeners`, `POST /api/listeners/{id}` for the owner key.
- MCP: `library_server(action="listeners")`.

## [0.4.65] - 2026-11-04

### Changed

- Server: «Unfamiliar» in My Wave skips tracks of favourite artists and albums, not just liked tracks.
- Android: 502/503/504 (VPS answering while the tunnel reconnects) count as offline: lists show downloads, the app looks
  for another address.
- Android: side swipe anywhere switches sub-tabs; label sheet colours current and changed labels; tiles for favourite
  artists; simpler first connection (one tap asks for access).
- Android Auto: «Recent» and offline playlists.

### Removed

- Android: the meaningless «My Wave is playing» banner in the full player.

## [0.4.64] - 2026-11-03

### Changed

- Desktop, Web, Android: a track with «feat. B» or «(with B)» also counts for guest B, who now appears in the artist
  list.
- A duet in the artist tag («A, B», «A и B», «A vs. B», «A & B») is split into two artists when at least one of them
  exists on their own («Simon & Garfunkel» stays one). File tags are not changed.
- Title parsing understands «(with B)», quoted «feat.», several guests and fixes mis-encoded guest names.

## [0.4.63] - 2026-11-03

### Added

- Android: Android Auto support: Wave, Favourites, Playlists and Downloads on the car screen, voice search, offline
  playback from downloads.

## [0.4.62] - 2026-11-03

### Added

- Desktop, Server, Android: artist photos from the Deezer catalogue (only the name is sent), else the cover of the
  biggest album; tiles view on the desktop and in the app; `/api/artist_image`.

### Fixed

- Desktop: `scripts/swap_build.sh` closes leftover background processes that kept files locked during an update.

## [0.4.61] - 2026-11-03

### Fixed

- Android: sheets close with a swipe down even when their content scrolls.

## [0.4.60] - 2026-11-03

### Changed

- Server, Android: share links are `<address>/open/track/5` and open straight in the app (App Link with
  `/.well-known/assetlinks.json`); without the app they open the web player. Old `#/track/…` links still work.
- Android: search and sorting scroll away with the list in «All».

### Added

- Desktop: «Share» in the player bar, on the artists page and in the album menu.

## [0.4.59] - 2026-11-03

### Added

- Android: four bottom tabs (Favourites, Wave, All, Playlists); artist sorting; language and voice filters for the wave;
  radio by track and artist; «Hide…» and «Mark for deletion…» for a track, album or artist; download progress bar;
  share links and a track page.

### Changed

- Android: changing the wave keeps the current track and replaces only the queue after it.

## [0.4.58] - 2026-11-03

### Added

- Desktop, Web, Server: radio by track and by artist; the wave can be narrowed by lyrics language and voice; a status
  line under the wave.
- Desktop: «Duplicates…» merges one artist spelled differently (catalogue only, likes follow).
- Desktop, Web: «Share» links without a key; a track page in the web player.
- Desktop: nightly sound-quality check (broken, truncated, transcoded, low quality) with a «Bad sound (N)» list; lists
  for re-downloading as JSON.
- Server: whole artists can be marked for deletion; the server tells clients its internet address.

### Changed

- «Unfamiliar» no longer offers favourites; artist and album counts exclude what the listener hid.
- Web: changing the wave keeps the current track; 👍 stays highlighted.

### Fixed

- Web: the bottom bar overlapped itself on portrait screens wider than 900 px.

## [0.4.57] - 2026-11-02

### Added

- Desktop: optional full mirror of the server's music (new files only, nothing overwritten or deleted); a «Server»
  button with sync state, pause, mirror on/off and a download speed limit.

## [0.4.56] - 2026-11-02

### Changed

- Desktop: all audio analysis runs on the PC; tracks missing here are fetched into a temporary cache and deleted after.
  New music found by the server gets its moods automatically after sync.

## [0.4.55] - 2026-11-01

### Fixed

- Desktop: the home server is found directly on the home network (the broadcast went out through the WSL adapter, so
  the app went through the VPS); while on the VPS it looks for the server at home every 5 minutes.

## [0.4.54] - 2026-11-01

### Added

- Desktop: works with a library on a home server: a local catalogue copy synced both ways every 30 s (likes, labels,
  hidden, delete marks, plays, playlists, analysis; the later edit wins); tracks missing here stream from the server;
  deleting moves the file to `.walle-trash` on the server.
- Server: change log and `/api/sync/pull`, `/api/sync/push`, `/api/tracks/{id}/trash` for the owner key; discovery over
  UDP at home and via the VPS outside.

## [0.4.53] - 2026-11-01

### Added

- Server: headless Linux server `python -m musicplayer.server --service` (serves the library, VPS tunnel, rescans every
  30 min, moods for new tracks, daily database backup); `scripts/build_linux_server.py` builds a package with a systemd
  service and can migrate the Windows data.

## [0.4.52] - 2026-10-24

### Fixed

- Desktop: a moved library (new drive letter, new folder) keeps its tracks with labels, likes and plays.
- Desktop: the VPS tunnel key gets owner-only permissions before connecting.

## [0.4.51] - 2026-10-16

### Changed

- Desktop: roomier mini player (420×104) with a volume button and a vertical slider.
- Build: version info in the exe and the installer, `build_installer.py --sign none`, a GitHub Actions release workflow.

## [0.4.49] - 2026-10-14

### Changed

- Desktop: denser mini player (360×78).

### Removed

- Desktop: the compact view (Ctrl+K); the full window and the mini player remain.

## [0.4.48] - 2026-10-14

### Added

- Desktop: like, labels and delete buttons in the mini player.

## [0.4.47] - 2026-10-14

### Added

- Desktop: artist and album in the player bar are links; the playing track's title on My Wave can be selected and
  copied.

## [0.4.46] - 2026-10-14

### Added

- Android: language, voice, recording type and era labels from the phone; a «Mark for deletion» button next to the like.

## [0.4.45] - 2026-10-14

### Added

- Desktop: labelling the playing track right on My Wave, applied to the track, album or artist.

## [0.4.44] - 2026-10-11

### Fixed

- Android: the full player closes with a swipe down again; «Save» in the mood and genre sheets is pinned to the bottom.

## [0.4.43] - 2026-10-11

### Added

- Android: «Mark for deletion» for a track or album in the track menu, with undo.

## [0.4.42] - 2026-10-11

### Added

- Desktop, Web, Server: listeners mark tracks or albums for deletion (`/api/delete-marks`); the desktop shows «For
  deletion (N)» with who and when, delete, keep, show; the list can be exported to JSON and loaded in another library.

## [0.4.41] - 2026-10-11

### Fixed

- Desktop: hidden genres and labels disappear from that listener's wave chips.
- Desktop: re-importing likes with dates fixes the times of likes already imported.

## [0.4.40] - 2026-10-11

### Changed

- Desktop: Favourites sorted by date added; any column sorts by its real value; the sort survives refreshes.

## [0.4.39] - 2026-10-11

### Added

- Desktop: likes and «Hide» on behalf of a chosen listener; a labels drop-down for the track, album or artist (Ctrl+L);
  like import takes the date from the file.

## [0.4.38] - 2026-10-11

### Added

- Android: a «Labels» button next to «Hide» for the playing track.

## [0.4.37] - 2026-10-11

### Changed

- Android: genres are entered in English only.

## [0.4.36] - 2026-10-11

### Changed

- Desktop, Web, Server: genres in English only (the server answers 400 with an example); known Russian tags are
  translated, unknown ones dropped; searching «рок» still finds Rock.

## [0.4.35] - 2026-10-11

### Changed

- Android: English genres of server 0.4.33+; stale Russian genres are removed from the saved wave choice.

## [0.4.34] - 2026-10-11

### Changed

- Desktop: Berlin School, NDH and Solo Acoustic Guitar placed in the genre tree.

## [0.4.33] - 2026-10-11

### Changed

- Desktop, Web, Server: English genre tree (Rock, Punk, Metal, Electronic, Hip-Hop, Pop, Jazz, Folk, Soundtrack,
  Oriental); Russian names stay synonyms; combined tags split; orphan genres attached to families; duplicates merged
  (62 names → 42).

## [0.4.32] - 2026-10-11

### Added

- Desktop, Web: a «Hide» button (crossed eye) next to the like for the playing track.

### Changed

- Desktop, Web: one «tag» icon for labelling everywhere.

## [0.4.31] - 2026-10-11

### Added

- Android: «Hide» next to the like for the playing track; one «tag» icon for labelling.

## [0.4.29] - 2026-10-11

### Changed

- Desktop: vector icons and tooltips in the mini player; each window mode remembers its own position.

## [0.4.28] - 2026-10-11

### Fixed

- Desktop: a custom genre path like «Metal/Sludge» no longer loses its sub-genre.

## [0.4.27] - 2026-10-11

### Added

- Android: custom genres as «Family/Sub-genre».

## [0.4.26] - 2026-10-11

### Added

- Desktop, Web, Server: custom genres join the genre tree (`custom_genres`) and get chips in the family colour.

## [0.4.25] - 2026-10-11

### Added

- Desktop, Web: genres as text paths («Punk/Pop Punk, Metal/Djent») with autocompletion.
- Android: hide a track, album, artist, genre or label, with undo and a «Hidden» screen; works offline for downloads.

## [0.4.24] - 2026-10-11

### Added

- Desktop, Web, Server: «Hide» for a track, album, artist, genre or label, per listener, with undo and a «Hidden» list
  (`/api/hidden`, MCP `hidden_items`).

### Fixed

- Desktop: a deleted file leaves the play queue and Favourites.

## [0.4.23] - 2026-10-11

### Fixed

- Desktop: deleting a track keeps the shown list and scroll and selects the next track.

## [0.4.22] - 2026-10-11

### Added

- Desktop: like import from text, CSV/TSV (Yandex Music, Spotify), JSON or M3U with fuzzy matching and a preview
  (MCP `import_likes`).

## [0.4.21] - 2026-10-10

### Changed

- Desktop: scanning shows real progress, counts and time left.

### Fixed

- Desktop: titles broken into «������» restored from MusicBrainz track lists (in the catalogue only).
- Desktop: .m4a tracks are analysed through ffmpeg.

## [0.4.20] - 2026-10-10

### Fixed

- Desktop: one data folder however the app is started (a packaged-app launcher redirected writes elsewhere).

## [0.4.19] - 2026-10-10

### Added

- Desktop: `WallePlayer.exe --quit` closes the running app cleanly (exit codes 0 / 1 / 2).

## [0.4.18] - 2026-10-10

### Fixed

- Desktop: in «Delete files?» Enter deletes and Escape cancels.

## [0.4.17] - 2026-10-10

### Added

- Desktop: the Ska-Punk sub-genre.

## [0.4.16] - 2026-10-09

### Changed

- Desktop: genre dictionary fixes (Art-Punk → Punk, junk genres removed).

## [0.4.15] - 2026-10-09

### Added

- Desktop: genre choice on My Wave by family colour; a family expands into sub-genres; the choice is saved.
- Android: genres from the library's dictionary grouped by family.

### Fixed

- Desktop: genre families have their colours again; «Retrain» works when an old model exists; no tunnel error on exit.
- Web: removing a family removes its sub-genres.

## [0.4.14] - 2026-10-09

### Changed

- Web: a desktop layout on the PC: full-screen player, a desktop-style bottom bar, a seek bar that catches clicks on its
  whole height, Esc closes windows.

## [0.4.13] - 2026-10-09

### Changed

- Desktop, Web, Server: one genre dictionary (70 names → 30); a sub-genre always carries its family; search
  understands English names.

## [0.4.11] - 2026-10-09

### Changed

- Server: access requests only from the home network unless allowed from the internet.

## [0.4.10] - 2026-10-09

### Added

- Server: internet access over https through your own VPS (Caddy with a free certificate on an sslip.io name).

### Fixed

- Server: the VPS tunnel no longer outlives the app.

## [0.4.9] - 2026-10-09

### Added

- Desktop: the app and installer are signed with a self-signed «Walle Player» certificate.

## [0.4.8] - 2026-10-08

### Fixed

- Android: Back on the home screen minimizes the app instead of closing it.

## [0.4.7] - 2026-10-08

### Fixed

- Android: no more drop to the connection screen when offline; a backup copy of the library list; «Send log» on the
  connection screen.

## [0.4.6] - 2026-10-08

### Added

- Server: `/api/info` lists all addresses of the library, home and external.
- Android: internet addresses arrive from the computer, so the phone connects outside home without a new QR; IPv6;
  a clear «wait N minutes» when banned; labels of downloads refresh without re-downloading.

## [0.4.5] - 2026-10-08

### Added

- Server: internet access options: static IP through the router (UPnP), your own VPS (reverse SSH tunnel), or your own
  address; brute-force protection also for visitors through the VPS.

## [0.4.4] - 2026-10-08

### Security

- Server: brute-force protection: 10 wrong keys in 10 minutes ban the address for an hour, growing to a week; IPv6 by
  /64; bans survive restarts and can be lifted in «Other devices».

## [0.4.3] - 2026-10-08

### Added

- Server: IPv6 access.
- Server: My Wave and sorting by plays follow each listener's own listening.

## [0.4.2] - 2026-10-08

### Added

- Web: works offline from downloads; playlists are remembered on the device.

## [0.4.1] - 2026-10-08

### Changed

- Desktop: scanning, training and labelling run in a separate process, so the player and the server don't freeze;
  the scan saves in small batches; model improvements (genre and artist features, descriptors v2).

## [0.4.0] - 2026-10-08

### Added

- Desktop: Windows installer (no admin rights, keeps the library data).
- Server: play deduplication by `uid`, resized covers `?size=`, `rev` for offline sync.

### Changed

- Desktop and Android versions aligned at 0.4.0.

## [0.3.0] - 2026-10-08

### Added

- Server, Web: the library on other devices: a web player with My Wave, library, artists, albums, playlists and likes;
  per-listener QR codes, likes and history; access requests by name; labelling from the phone; downloads and offline
  playback; internet access through an external address; tray mode and start with Windows.

## [0.2.0]

### Added

- Desktop: catalogue and player, mood model (descriptors, CLAP, honest validation), My Wave, labelling feed, MCP server.

[Unreleased]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.72...HEAD
[0.4.72]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.70...v0.4.72
[0.4.70]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.51...v0.4.70
[0.4.51]: https://github.com/mitay-walle/walle-player-desktop/compare/v0.4.10...v0.4.51
[0.4.10]: https://github.com/mitay-walle/walle-player-desktop/releases/tag/v0.4.10
