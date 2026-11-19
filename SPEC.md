# engavenRDP — Product & Engineering Specification

*Rewritten and expanded from the original brief. This is the spec the code in this repository implements.*

## 1. Goal

Build **engavenRDP**: a single-window desktop application for managing and using
many Remote Desktop (RDP) connections at once. A systems administrator should be able to:

* keep hundreds of servers organised in a tree of files and groups,
* define connection settings and credentials **once** high in the tree and have every
  server below inherit them,
* connect to one server or a whole group with a single action,
* watch every open session at a glance as live, scaled thumbnails, and
* jump into any session full size (or full screen) without juggling separate windows.

Non-goals for v1: VNC/SSH protocols, a password manager, multi-user sync.

## 2. Platform & stack

| Concern | Decision | Why |
|---|---|---|
| Language | Python ≥ 3.10 | Fast to iterate, packages cleanly with pipx |
| GUI | PySide6 (Qt 6) | Native look on GNOME/KDE, model/view tree, good X11 interop |
| RDP engine | FreeRDP **2.x or 3.x** (`xfreerdp` / `xfreerdp3`) launched as a child process | Mature, maintained, packaged everywhere |
| Embedding | FreeRDP `/parent-window:<XID>` into a native Qt widget, plus `/smart-sizing` for scaling | Sessions live *inside* the app; thumbnails are the real sessions scaled down |
| Window plumbing | `python-xlib` (resize the embedded child, send special keys) | Pure Python, no compiler needed |
| Display server | X11; on Wayland the app runs through XWayland (`QT_QPA_PLATFORM=xcb`) | `/parent-window` is an X11 feature |
| Config format | JSON (`*.erdp`) — human-readable, diff-friendly; **import** of Windows `.rdg` XML | Easy to version in git; painless migration from Windows |
| Secrets | Fernet (AES-128-CBC + HMAC) with a key that is either (a) a random master key kept in the desktop keyring (Secret Service / libsecret) or (b) derived from a user passphrase with scrypt | Linux-native, no plaintext passwords on disk |
| Packaging | `pipx install .`, plus an `install.sh` that adds a `.desktop` launcher | Simple, no root needed |

The RDP engine sits behind an abstract `RdpBackend` interface so FreeRDP can later be swapped
(e.g. for a libfreerdp binding, Remmina plugin or GTK-VNC style widget) without touching the UI.

## 3. Data model

```
Workspace
 └─ File (.erdp)                  ← is itself a group: settings + credential profiles + encryption header
     ├─ Group
     │   ├─ Group …               (nesting unlimited)
     │   └─ Server …
     ├─ SmartGroup (rules)        ← membership computed, never stored
     └─ Server
```

* Every node has a stable UUID, a name, and a free-text comment.
* **Container rule:** a file or group holds *either* groups/smart groups *or* servers — never both
  at the same level (the UI enforces this on add, paste and drag-and-drop).
* Servers have: host (name or IP), optional display name, optional per-server port override
  (via Connection settings), comment.

### 3.1 Setting categories and inheritance

Each node (file, group, server) carries one record per category. A record is either
`{"inherit": true}` or `{"inherit": false, …values}`.

| Category | Fields |
|---|---|
| **Logon credentials** | credential profile *or* username / domain / password; "prompt for password" |
| **Connection** | port, connect to console/admin session, start program, working directory, load-balance info, auto-reconnect |
| **Gateway** | enabled, host, port, use logon credentials *or* own username / domain / password, bypass for local addresses |
| **Display** | mode (fit to client area / fixed size / full screen), width, height, colour depth, smart sizing, open embedded or in an external window, multi-monitor (external window only), monitor list |
| **Local resources** | clipboard, home drive, extra drive paths, printers, audio playback (local / remote / off), microphone, smart cards |
| **Security** | protocol (auto / NLA / TLS / RDP), certificate policy (trust on first use / ignore / ask), restricted admin mode |
| **Experience** | network type (auto / LAN / broadband / modem), wallpaper, themes, font smoothing, GFX pipeline |

**Resolution:** when connecting, walk from the server up through groups to the file; the first
record that does not inherit wins; if none is found, built-in defaults apply. The Properties
dialog shows the effective values *and where each one came from*.

### 3.2 Credential profiles

Named credential sets (username, domain, password). Two scopes:
* **File profiles** — stored (encrypted) inside the `.erdp` file, travel with it.
* **Global profiles** — stored in `~/.config/engavenrdp/profiles.json`, available to every file.

A Logon record can reference a profile by name; file scope is searched before global.

### 3.3 Smart groups

A smart group holds a list of rules and a match mode (all / any). Rule = *field* (name,
display name, host, comment, parent group path) × *operator* (contains, does not contain,
equals, starts with, ends with, regex) × *value*. Membership is recomputed live from the
servers in the same file.

### 3.4 Virtual groups (always present, top of the tree)

* **Connected** — every session currently connecting or connected.
* **Favorites** — servers the user starred.
* **Recent** — last 20 servers connected to.
* **Reconnect** — servers being retried: the app probes the RDP port every N seconds and
  connects as soon as it answers (useful after a reboot).

Favorites and Recent are stored by `file path + server UUID` in the app state.

## 4. Encryption & graceful failure

* Passwords are stored as `enc:v1:<fernet-token>`; plaintext never touches disk.
* Each file declares its key source in an `encryption` header:
  * `keyring` — random 256-bit key in the Secret Service under the file's key-id
    (`keyring` Python package if installed, else the `secret-tool` CLI);
  * `passphrase` — scrypt(salt, passphrase); a verifier token detects a wrong passphrase.
* **Opening a file whose key is unavailable** (another machine, keyring locked, passphrase
  cancelled) never fails: the file opens *locked*, a yellow banner explains why, the encrypted
  values are preserved byte-for-byte on save, and the user is prompted for a password at
  connect time. A "Re-key file" action lets the user set a new key and re-enter passwords.

## 5. Sessions

* **Connect / disconnect / reconnect** a server, a group (recursively), a smart group or a
  virtual group. Group connects are staggered (configurable delay) so a 50-server group does
  not stampede.
* **States:** disconnected ▸ connecting ▸ connected ▸ (error). Shown as a coloured dot on the
  tree icon, in the session header and on the thumbnail.
* **Client area:**
  * server selected → its session full size (embedded); if not connected, a card with
    *Connect* and the last error;
  * group selected → a grid of thumbnails of that group's servers. Connected sessions are the
    live session scaled down; click selects, double-click opens full size. Thumbnail size is
    adjustable (slider / Ctrl+wheel).
* **Full screen:** detach the session into a borderless window on the monitor the app is on
  (or a chosen monitor), with a slide-down connection bar (Restore / Send keys / Disconnect).
* **Special keys:** Ctrl+Alt+Del, Ctrl+Shift+Esc, Alt+Tab, Alt+F4, Win, Win+L, Win+R, Print Screen
  — sent directly to the embedded FreeRDP window so the local desktop never sees them.
* **External window mode** (per-node Display setting) for true multi-monitor spans
  (`/multimon`, `/monitors:`).
* Process output is captured into a per-session log viewable from the session header.
* Exit codes are translated to human-readable reasons (authentication failure, DNS error…).

## 6. Server management

* Add server (host, display name, comment).
* **Bulk add** with patterns, live preview and a 2 000-host safety cap:
  `web[01-10]` (zero padding kept), `node[a-f]`, `{prod,stage}-db[1-5]`, combinations.
* **Import from text file:** one host per line; `host  display name` supported; `#` comments.
* **Import Windows `.rdg`:** groups, servers, display names, comments, usernames/domains
  (Windows DPAPI-encrypted passwords cannot be decrypted on Linux and are dropped, with a notice).
* **Export / import a group** as a standalone `.erdp` fragment.
* Cut / copy / paste, duplicate, rename (F2), delete with confirmation.
* Drag-and-drop to move servers and groups between groups and files (container rule enforced).

## 7. UI / UX

* Modern Fusion-based theme, light and dark, follows the system by default.
* Left: search box + tree. Right: client area. Bottom: status bar with session counts.
* Toolbar with the most common actions; every action is also in a context menu.
* **Keyboard shortcuts**

| Action | Shortcut |
|---|---|
| Connect | Enter / Ctrl+Enter |
| Disconnect | Ctrl+Shift+D |
| Reconnect | F5 |
| Properties | Alt+Enter |
| Add server / group / smart group | Ctrl+N / Ctrl+G / Ctrl+Shift+G |
| Bulk add | Ctrl+B |
| Rename / Delete | F2 / Del |
| Find in tree | Ctrl+F |
| Toggle full screen | F11 |
| New / Open / Save file | Ctrl+Shift+N / Ctrl+O / Ctrl+S |
| Toggle favorite | Ctrl+K |

* **Auto-save** (toggle) — saves 1.5 s after each change; otherwise unsaved files are marked
  with `*` and the user is asked on exit.
* Session restore: re-open last files, remember window geometry, splitter, expanded groups.

## 8. Command line

```
engavenrdp [FILE ...]                       open files (in addition to the remembered ones)
engavenrdp --connect web01,db02             connect to servers by name/display name/host
engavenrdp --connect-group "Prod/Web"       connect a whole group (path from file root)
engavenrdp --list FILE                      print the tree and exit
engavenrdp --check                          show detected FreeRDP version / capabilities and exit
```

## 9. Architecture

```
engavenrdp/
  model/      settings.py (category schemas + defaults) · nodes.py · resolve.py
              storage.py (JSON + .rdg import) · patterns.py · smartgroups.py
  security/   crypto.py (Fernet, scrypt, keyring) · vault.py
  backend/    base.py (RdpBackend, SessionSpec) · freerdp.py · x11.py
  core/       session.py (state machine, QProcess) · manager.py (sessions, reconnect queue)
              appstate.py (prefs, favorites, recent)
  ui/         main_window · tree · client_area · thumbnails · fullscreen · dialogs/* · icons · theme
  cli.py, __main__.py
tests/        model, inheritance, patterns, storage round-trip, crypto, FreeRDP argument building
```

## 10. Security notes

* Passwords are handed to FreeRDP through **stdin** (`/args-from:stdin` on FreeRDP 3,
  `/from-stdin` on FreeRDP 2) — never on the command line where `ps` would show them.
* Certificate default is *trust on first use*; "ignore" is available but labelled unsafe.
* When a server presents a different certificate from the saved one, FreeRDP asks on its terminal,
  where nobody can answer. The session recognises the question, stops FreeRDP and shows both
  certificates. "Trust new certificate and connect" removes the saved certificate (FreeRDP 3:
  `~/.config/freerdp/server/<host>_<port>.pem`; FreeRDP 2: the line in `known_hosts2`) and connects
  again; if the certificate saved by that connection is not the one that was shown, the session is
  stopped and the certificate forgotten again.
* Decrypted secrets live only in memory.

## 11. Acceptance checklist

- [ ] Create a file, nested groups, servers; save; reopen; identical tree.
- [ ] Set credentials on a group; a server two levels down connects with them; override on the server wins.
- [ ] `web[01-03]` adds three servers.
- [ ] Smart group "name contains db" shows matching servers live.
- [ ] Connect a group → thumbnails appear and update; double-click → full size; F11 → full screen.
- [ ] Ctrl+Alt+Del reaches the remote session.
- [ ] Open the file as another Linux user → banner, file still usable, passwords preserved on save.
- [ ] `engavenrdp --connect web01` connects on start-up.
