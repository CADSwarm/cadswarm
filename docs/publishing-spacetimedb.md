# Publishing the module to SpacetimeDB (Maincloud)

Republish whenever `module/` or `rules/` changes. The rules crate is compiled into the module, so new op
kinds (for example the style ops `style_context`, `teach_style`, `resolve_style`, `learn_style`) are
refused by a database still running the old module. `--delete-data=never` keeps the team's data; if the
new schema cannot migrate, the publish is refused instead of wiping anything.

## 1. Sign in as the database's owner

Only the identity that created a database may publish to it.

```powershell
$st = "$env:LOCALAPPDATA\SpacetimeDB\spacetime.exe"
& $st login                  # opens a browser; use the account that owns the database
& $st login show             # your identity
# The owner, to compare with:
(Invoke-RestMethod https://maincloud.spacetimedb.com/v1/database/cadswarm-calhacks-ca8c).owner_identity
```

If the two identities differ, the owner has to publish (or you create a new database under your own
login, and everyone joins it with new invites, since existing invites point at the old one).
"Invalid token: InvalidSignature" means the stored login is stale: run `spacetime login` again.

## 2. Build and publish

```powershell
cd cadswarm
rustup target add wasm32-unknown-unknown    # once
cargo build --release --target wasm32-unknown-unknown --manifest-path module\Cargo.toml
& $st publish --server maincloud --yes=all --delete-data=never `
    --bin-path module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm  cadswarm-calhacks-ca8c
```

Always pass `--server`: the CLI's default may be Maincloud or local depending on the machine
(`spacetime server list` shows it).

## 3. Bindings (only if tables or reducers changed)

The host and web app use generated bindings. Regenerate them and commit the result:

```powershell
& $st generate --lang rust --out-dir host\src\module_bindings --module-path module
& $st generate --lang typescript --out-dir web\src\module_bindings --module-path module
```

## 4. Restart the host and set up Elastic

The host must run the matching build, so stop it, rebuild and start it again (the demo script does all
of it, with the keys from `..\cadswarm.env`):

```powershell
powershell -File scripts\demo-down.ps1
powershell -File scripts\demo-up.ps1 -Db cadswarm-calhacks-ca8c
```

New Elastic indices and agents (idempotent; safe to run again):

```powershell
powershell -File scripts\with-env.ps1 .venv\Scripts\python.exe -c "import json; from cadswarm.knowledge import Elastic; e = Elastic.from_env(); print(e.setup(json.load(open('elastic/setup.json')))); e.install_agent('elastic')"
```

## 5. Check it

```powershell
& $st sql --server maincloud cadswarm-calhacks-ca8c "SELECT name, kind, status FROM v_member"
```

The host shows up as `active`. In FreeCAD, the Tools tab › Learned design style… opens without an error.
To try everything first on a throwaway local database:
`spacetime start`, then `scripts\publish-local.ps1 -Db cadswarm-e2e` and
`$env:CADSWARM_LIVE = "1"; $env:CADSWARM_DB = "cadswarm-e2e"; python -m pytest tests/test_space_live.py -q`.
