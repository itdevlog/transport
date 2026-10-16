# Per-User Favorites (Anonymous Cookie Profiles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each browser its own favorites (routes, stops, notes, direction labels) via an anonymous cookie uid, stored per-user in SQLite.

**Architecture:** A new `profiles.py` owns per-user profile storage and mutation (SQLite `profiles.db`, one row per uid). `server.py` reads/mints the `marshrut_uid` cookie, serves `/api/favorites` from the caller's profile, and one-shot migrates the legacy shared `favorites.json` into the first caller's profile.

**Tech Stack:** Python stdlib only — `sqlite3`, `http.cookies`, `uuid`, `re`, `json`, `threading`; `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-16-user-profiles-design.md`

## Global Constraints

- Python stdlib only. No new dependencies.
- uid = `uuid4().hex`, must match `^[0-9a-f]{32}$`.
- Cookie `marshrut_uid`: `HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000` (+ `Secure` when the request is https).
- `profiles.db` lives next to `server.py` (path from config), git-ignored; open SQLite in WAL mode.
- All existing API response shapes stay identical; all existing tests stay green.
- YAGNI: no login, no account recovery, no profile sharing, no dead-profile cleanup.
- Profile shape is exactly the legacy one: `{"routes": [ {id, number, name} ], "stops": [ {id, name, note?, dirs?} ]}`.

## Review Focus

1. **Cookies disabled** — every request arrives without a cookie; the API must still answer 200 with an empty profile and never crash. (Test in Task 3.)
2. **Corrupt/undreadable `profiles.db`** — the server must still start and `/api/favorites` must return an empty profile, not 500. (Test in Task 5.)
3. **Forced/garbage uid cookie** — a value not matching `^[0-9a-f]{32}$` must be replaced with a fresh uid. (Test in Task 3.)
4. **Corrupt profile row (non-JSON / wrong shape)** — must read back as an empty profile. (Test in Task 1.)
5. **Corrupt legacy `favorites.json`** — migration must ignore it, mark the file migrated, and leave the caller with an empty profile. (Test in Task 4.)

---

### Task 1: `ProfilesStore` skeleton (schema, get, save)

**Files:**
- Create: `profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `profiles.KIND_KEYS = {"route": "routes", "stop": "stops"}`; `profiles.EMPTY_PROFILE` (a fresh `{"routes": [], "stops": []}` factory via `empty_profile()`); `class ProfilesStore(db_path)` with `get(uid) -> dict` and `save(uid, data) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py
import json
import os
import sqlite3
import tempfile
import unittest

import profiles


class ProfilesStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "profiles.db")
        self.store = profiles.ProfilesStore(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_profile_is_empty(self):
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_save_and_get_roundtrip(self):
        data = {"routes": [{"id": 1554, "number": "87", "name": "R"}], "stops": []}
        self.store.save("a" * 32, data)
        self.assertEqual(self.store.get("a" * 32), data)

    def test_profiles_isolated_by_uid(self):
        self.store.save("a" * 32, {"routes": [{"id": 1}], "stops": []})
        self.store.save("b" * 32, {"routes": [], "stops": [{"id": 2}]})
        self.assertEqual(self.store.get("a" * 32)["stops"], [])
        self.assertEqual(self.store.get("b" * 32)["routes"], [])

    def test_corrupt_row_returns_empty(self):
        self.store.save("a" * 32, {"routes": [], "stops": []})
        con = sqlite3.connect(self.path)
        con.execute("UPDATE profiles SET data = ? WHERE uid = ?", ("{not json", "a" * 32))
        con.commit()
        con.close()
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_wrong_shape_row_returns_empty(self):
        self.store.save("a" * 32, {"routes": [], "stops": []})
        con = sqlite3.connect(self.path)
        con.execute("UPDATE profiles SET data = ? WHERE uid = ?", ("[1,2,3]", "a" * 32))
        con.commit()
        con.close()
        self.assertEqual(self.store.get("a" * 32), {"routes": [], "stops": []})

    def test_upsert_overwrites(self):
        self.store.save("a" * 32, {"routes": [], "stops": [{"id": 1}]})
        self.store.save("a" * 32, {"routes": [], "stops": []})
        self.assertEqual(self.store.get("a" * 32)["stops"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_profiles -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'profiles'`.

- [ ] **Step 3: Implement `profiles.py`**

Module-level: `SCHEMA_VERSION = 1`; `KIND_KEYS = {"route": "routes", "stop": "stops"}`; `def empty_profile(): return {"routes": [], "stops": []}`; `_SCHEMA = "CREATE TABLE IF NOT EXISTS profiles (uid TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER NOT NULL)"`.

`class ProfilesStore:` with `__init__(self, db_path)` (store `self._db_path`, call `self._init_db()`), `_connect(self)` (`sqlite3.connect(self._db_path)`, `row_factory = sqlite3.Row`), `_init_db(self)` (`conn.executescript(_SCHEMA)` inside `with conn:`, set `PRAGMA journal_mode=WAL`), `save(self, uid, data)` (upsert `INSERT ... ON CONFLICT(uid) DO UPDATE SET data=excluded.data, updated=excluded.updated`), and `get(self, uid)` returning `empty_profile()` on missing/corrupt/wrong-shape rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_profiles -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add profiles.py tests/test_profiles.py
git commit -m "feat(profiles): SQLite per-user profile store (get/save)"
```

---

### Task 2: Profile mutations (`add/remove/note/dir`)

**Files:**
- Modify: `profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `ProfilesStore.get/save` from Task 1; `KIND_KEYS`.
- Produces: `ProfilesStore.add(uid, kind, item)`, `remove(uid, kind, item_id)`, `set_note(uid, kind, item_id, note)`, `set_dir_label(uid, kind, item_id, where, label)` — each mutates the uid's profile, persists, and returns the updated profile dict.

- [ ] **Step 1: Write the failing test** — add `ProfilesMutationTest` to `tests/test_profiles.py`:

```python
class ProfilesMutationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = profiles.ProfilesStore(os.path.join(self.dir.name, "p.db"))
        self.uid = "c" * 32

    def tearDown(self):
        self.dir.cleanup()

    def test_add_and_dedupe(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "Академия тенниса"})
        self.store.add(self.uid, "stop", {"id": 5830, "name": "dup"})
        self.assertEqual(len(self.store.get(self.uid)["stops"]), 1)

    def test_remove(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.remove(self.uid, "stop", 5830)
        self.assertEqual(self.store.get(self.uid)["stops"], [])

    def test_set_note_persists_and_clears(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.set_note(self.uid, "stop", 5830, "в школу")
        self.assertEqual(self.store.get(self.uid)["stops"][0]["note"], "в школу")
        self.store.set_note(self.uid, "stop", 5830, "")
        self.assertNotIn("note", self.store.get(self.uid)["stops"][0])

    def test_set_dir_label_and_clear(self):
        self.store.add(self.uid, "stop", {"id": 5830, "name": "A"})
        self.store.set_dir_label(self.uid, "stop", 5830, "Академика Ландау", "домой")
        self.assertEqual(self.store.get(self.uid)["stops"][0]["dirs"],
                         {"Академика Ландау": "домой"})
        self.store.set_dir_label(self.uid, "stop", 5830, "Академика Ландау", "")
        self.assertNotIn("dirs", self.store.get(self.uid)["stops"][0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_profiles -v`
Expected: FAIL with `AttributeError: 'ProfilesStore' object has no attribute 'add'`.

- [ ] **Step 3: Implement** `add`, `remove`, `set_note`, `set_dir_label` on `ProfilesStore`, mirroring the existing `FavoritesStore` semantics: `add` dedupes by `str(item["id"])`; `set_note` strips the note, sets/removes the `note` key; `set_dir_label` strips `where`/`label`, sets/removes `dirs[where]`, drops an emptied `dirs`. Each does `data = self.get(uid); <mutate>; self.save(uid, data); return data`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_profiles -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add profiles.py tests/test_profiles.py
git commit -m "feat(profiles): add/remove/note/dir mutations per user"
```

---

### Task 3: Cookie identity + favorites from profiles

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `ProfilesStore` (get/save/add/remove/set_note/set_dir_label), `KIND_KEYS`, `MAX_NOTE_LEN`.
- Produces: `_valid_uid(value) -> bool`; `_new_uid() -> str`; `_uid_from_cookie(cookie_header) -> str | None`; `handle_api(..., profiles=None, uid=None)`; `make_handler(portal, store, static_dir, catalog=None, profiles=None)`; `serve(config, portal=None, store=None, static_dir="static", catalog=None, profiles=None)` (pass-through; Task 5 later builds it from config).

- [ ] **Step 1: Write the failing test** — add to `tests/test_server.py` (imports `re`, `http.client` already available; add `profiles` import):

```python
class FavoritesCookieTest(unittest.TestCase):
    # reuse the HttpServerTest socket pattern: serve(cfg, portal=FakePortal(),
    # store=FavoritesStore(tmp), static_dir=tmp, profiles=ProfilesStore(tmp/profiles.db))
    def test_first_request_sets_cookie_and_empty_profile(self):
        # GET /api/favorites with no Cookie header
        # 200, body {"routes": [], "stops": []}, Set-Cookie contains "marshrut_uid="
        # and "HttpOnly" and "SameSite=Lax" and "Max-Age=31536000"

    def test_same_cookie_returns_same_profile(self):
        # POST add {kind:"stop", id:5830, name:"A"} with cookie c1
        # GET with cookie c1 -> one stop; GET with cookie c2 -> empty

    def test_garbage_cookie_is_replaced(self):
        # GET with Cookie: marshrut_uid=nothex  -> Set-Cookie marshrut_uid=<32 hex>
```

Write the three tests explicitly (mirroring `HttpServerTest`'s setUp/tearDown with a real `serve(...)`), asserting the exact strings above.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server.FavoritesCookieTest -v`
Expected: FAIL — `serve()` has no `profiles` parameter / no `Set-Cookie`.

- [ ] **Step 3: Implement**

In `server.py`: add `import http.cookies`, `import re`, `import uuid`, and `from profiles import ProfilesStore`. Add module helpers `_valid_uid(value)` (`bool(re.fullmatch(r"[0-9a-f]{32}", value or ""))`), `_new_uid()` (`uuid.uuid4().hex`), `_uid_from_cookie(header)` (parse with `http.cookies.SimpleCookie`, swallow `CookieError`, return the `marshrut_uid` value or `None`).

Extend `handle_api(path, params, method, body, portal, store, catalog=None, profiles=None, uid=None)`: when `profiles is not None`, serve `/api/favorites` GET from `profiles.get(uid)` and POST through `profiles.add/remove/set_note/set_dir_label`; when `profiles is None`, keep the existing `store` behavior unchanged.

Extend `make_handler(portal, store, static_dir, catalog=None, profiles=None)`: in `do_GET`/`do_POST` compute `uid = _uid_from_cookie(self.headers.get("Cookie"))`; if invalid, `uid = _new_uid()` and mark the response to include `Set-Cookie: marshrut_uid=<uid>; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000` (+ `; Secure` when `self.headers`/scheme indicates https — use the `X-Forwarded-Proto` header set by Caddy, else `False`). Pass `profiles=profiles, uid=uid` to `handle_api`; `_send` gains an optional `set_uid` argument that appends the header. Also add `profiles=None` to `serve(...)` and forward it to `make_handler` (Task 5 will construct the store from config when it is not passed).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests`
Expected: PASS, all tests including `FavoritesCookieTest`.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): anonymous cookie uid for per-user favorites"
```

---

### Task 4: One-shot legacy migration

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `ProfilesStore`, `FavoritesStore`.
- Produces: `_maybe_migrate(profiles, store, uid) -> None`.

- [ ] **Step 1: Write the failing test** — add `MigrationTest` to `tests/test_server.py`:

```python
class MigrationTest(unittest.TestCase):
    def test_legacy_adopted_once_and_renamed(self):
        # FavoritesStore(path) with one stop saved; ProfilesStore empty
        # _maybe_migrate(profiles, store, uid1) -> profiles.get(uid1) has the stop
        # os.path.exists(path) is False; os.path.exists(path + ".migrated") is True
        # _maybe_migrate(..., uid2) -> profiles.get(uid2) is empty

    def test_corrupt_legacy_is_ignored(self):
        # write "not json" to the legacy path
        # _maybe_migrate(...) -> uid profile empty, legacy renamed to .migrated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server.MigrationTest -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_maybe_migrate'`.

- [ ] **Step 3: Implement** `_maybe_migrate(profiles, store, uid)`: return early if no legacy file (`os.path.exists(store.path)`); read `store.load()`; if it has any routes/stops and the uid's profile is empty, `profiles.save(uid, data)`; then rename the legacy file to `store.path + ".migrated"` (`os.replace`), swallowing `OSError`. Wrap the read/migrate in `try/except (OSError, ValueError)` so a corrupt legacy file is ignored but still renamed. Call `_maybe_migrate` at the start of the `/api/favorites` branch in `handle_api` (only when `profiles is not None`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): migrate legacy favorites.json into first profile"
```

---

### Task 5: Wiring, config, and docs

**Files:**
- Modify: `server.py`, `config.json`, `.gitignore`, `README.md`, `DEPLOY.md`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `ProfilesStore`, `serve`.
- Produces: `DEFAULTS["profiles"] = {"path": "profiles.db"}`; `serve(config, ..., profiles=None)` constructs a `ProfilesStore` from config when `profiles` is not passed.

- [ ] **Step 1: Write the failing test** — add `ProfilesConfiguredStartupTest` to `tests/test_server.py`:

```python
class ProfilesConfiguredStartupTest(unittest.TestCase):
    def test_serve_creates_profiles_store(self):
        # cfg = dict(CONFIG_HTTP, host="127.0.0.1", port=0,
        #            profiles={"path": os.path.join(tmp, "profiles.db")})
        # GET /api/favorites -> 200 and Set-Cookie; profiles.db exists

    def test_corrupt_profiles_db_falls_back(self):
        # write garbage into profiles.db, then serve; GET /api/favorites -> 200,
        # empty profile (not 500)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server.ProfilesConfiguredStartupTest -v`
Expected: FAIL — `serve()` does not build a `ProfilesStore`.

- [ ] **Step 3: Implement** `DEFAULTS["profiles"] = {"path": "profiles.db"}`; in `serve(config, portal=None, store=None, static_dir="static", catalog=None)` build `profiles = ProfilesStore(config.get("profiles", {}).get("path", "profiles.db"))` inside `try/except (sqlite3.Error, OSError)` → `profiles = None`; pass `profiles=profiles` to `make_handler`. Add `"profiles": {"path": "profiles.db"}` to `config.json`; add `profiles.db` and `favorites.json.migrated` to `.gitignore`; add a README bullet + config-table row; note in DEPLOY.md that `ReadWritePaths=/opt/transport` already covers `profiles.db`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add server.py config.json .gitignore README.md DEPLOY.md tests/test_server.py
git commit -m "feat(server): wire per-user profiles store; config and docs"
```
