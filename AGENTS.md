# AGENTS.md - Decky Loader Plugin Development

> Instructions for AI agents (Claude Code, Cursor, Cline, etc.) to create and maintain a Decky Loader plugin using the official template.

**Template Source:** https://github.com/SteamDeckHomebrew/decky-plugin-template
**Template Commit:** 90d0780e882a17f5714fc6de044c645f22608290 (2026-04)
**Docs:** https://wiki.deckbrew.xyz/en/plugin-dev/getting-started
**Store DB:** https://github.com/SteamDeckHomebrew/decky-plugin-database

---

## 1. Goal

Create a new Decky Loader plugin from scratch that:
- Builds with `pnpm` v9 + Rollup (`@decky/rollup`)
- Uses `@decky/ui` for native Steam Deck UI and `@decky/api` for frontend<->backend RPC
- Follows the shippable layout required by Decky Store CI
- Is ready to deploy to a real Steam Deck via Decky CLI

## 2. Prerequisites

**ALWAYS check and enforce:**
- Node.js >= 16.14+ (recommend 18+)
- `pnpm@9` EXACTLY. Install via: `sudo npm i -g pnpm@9`
- If custom compiled backends are needed: Docker must be installed (used by Decky CLI)
- Python 3.11+ on Deck (backend runs on Deck)
- Decky Loader installed on target Deck + Developer Mode enabled (Decky > Settings > General > Developer Mode)

If you get build errors after a Decky update: `pnpm update @decky/ui --latest`

## 3. Initialization

When asked to create a new plugin, do this:

1.  **Clone from template:** Use GitHub "Use this template" button or:
    ```bash
    git clone https://github.com/SteamDeckHomebrew/decky-plugin-template my-plugin
    cd my-plugin
    rm -rf .git
    git init
    ```

2.  **Install and build:**
    ```bash
    pnpm i
    pnpm run build
    ```

3.  **VSCode workflow (if available):** Run tasks `setup` and `build` and `deploy`. If not using VSCode, use the equivalent pnpm/cli commands manually.

4.  **Rename:** Edit `plugin.json` name, `package.json` name (must be lowercase-hyphens), `rollup.config.js` if needed, and README/LICENSE.

## 4. Plugin Anatomy (Required Structure)

```
plugin-root/
├── src/index.tsx        # frontend entry — must export definePlugin()
├── main.py              # backend entry — class Plugin with async methods = RPC surface [required if using backend]
├── py_modules/          # extra Python modules, auto-added to sys.path by Decky
├── backend/src|out/     # compiled-binary backends ONLY; only backend/out/ ships to store
├── defaults/            # static files copied into package (configs, web assets)
├── assets/              # images (store listing image must be PNG)
├── plugin.json          # Decky metadata [required]
├── package.json         # name (lowercase-hyphens), version — bump before every release [required]
├── rollup.config.js     # should stay a one-liner around @decky/rollup
├── tsconfig.json
└── dist/index.js        # Rollup build output [shipped, required]
```

**Shipped ZIP layout (what Decky installs):**
```
pluginname/
├── dist/index.js [required]
├── bin/ (optional, contains binaries from backend/out)
├── plugin.json [required]
├── package.json [required]
├── main.py (if using python backend)
├── py_modules/ (if any)
├── LICENSE [required]
└── README.md (recommended)
```
Anything else (src/, tests/) must NOT ship.

## 5. Development Workflow

### Frontend changes
Every time you change `src/index.tsx` etc, you MUST rebuild:
```bash
pnpm run build
# or watch mode
pnpm run watch
```

### Build deployable package
```bash
# If Decky CLI is installed in repo as ./cli/decky
./cli/decky plugin build "$(pwd)"

# If installed globally
npm install -g @decky/cli
decky plugin build .
```

### Deploy to Deck
```bash
decky plugin deploy -i <STEAM_DECK_IP>   # password 'deck' by default
# fallback manual:
# copy to /home/deck/homebrew/plugins/<pluginname>/ and restart Decky
```

### CEF Debugging
For UI debugging: https://wiki.deckbrew.xyz/en/plugin-dev/cef-debugging

## 6. Frontend Rules (@decky/ui + @decky/api)

**Modern API (api_version 1) — USE THIS, not legacy ServerAPI:**

```tsx
import { definePlugin, callable, addEventListener, removeEventListener, toaster } from "@decky/api";
import { PanelSection, PanelSectionRow, ButtonItem, staticClasses } from "@decky/ui";
import { FaPlug } from "react-icons/fa";

// Define RPC typed to Python method
const myMethod = callable<[first: number, second: number], number>("my_method");

export default definePlugin(() => {
  const listener = addEventListener<[msg: string]>("my_event", (msg) => {
    toaster.toast({ title: msg });
  });

  return {
    name: "My Plugin",
    titleView: <div className={staticClasses.Title}>My Plugin</div>,
    content: <Content />,
    icon: <FaPlug />,
    onDismount() { removeEventListener("my_event", listener); }
  };
});

function Content() {
  return (
    <PanelSection>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => {
          const n = await myMethod(1, 2);
        }}>Call Backend</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
```

**Rules:**
- UI MUST use `@decky/ui` components for gamepad focus (PanelSection, ToggleField, SliderField, DropdownItem, etc.)
- React and react-dom come from Steam client at runtime — NEVER add them as real dependencies, only `@types/react` is allowed
- Keep `rollup.config.js` minimal
- All callables should live in `src/services/api.ts` with TS types matching Python return shapes
- For routes outside Quick Access: `routerHook.addRoute("/my-page", Component)` and remove in `onDismount`

## 7. Backend Rules (Python)

```python
import os
import asyncio
import decky
from settings import SettingsManager

class Plugin:
    async def my_method(self, first: int, second: int) -> int:
        decky.logger.info(f"called with {first}, {second}")
        return first + second

    async def _main(self):
        # Runs as task for plugin lifetime - MUST NOT BLOCK
        self.loop = asyncio.get_event_loop()
        self.settings = SettingsManager(name="settings", settings_directory=os.environ["DECKY_PLUGIN_SETTINGS_DIR"])
        self.settings.read()
        decky.logger.info("Plugin loaded")

    async def _unload(self):
        # Called on Decky reload/shutdown
        pass

    async def _uninstall(self):
        # Called on removal — clean up system remnants
        pass

    async def _migration(self):
        # Runs before _main, use decky.migrate_logs/settings/runtime
        decky.migrate_logs()
        decky.migrate_settings()
```

**The `decky` module provides:**
- `decky.logger` — logs go to `DECKY_PLUGIN_LOG_DIR`
- `await decky.emit("event_name", *args)` — push to frontend
- Path env vars: `DECKY_PLUGIN_SETTINGS_DIR`, `DECKY_PLUGIN_RUNTIME_DIR`, `DECKY_PLUGIN_LOG_DIR`, `DECKY_PLUGIN_DIR`, `DECKY_USER_HOME`, `DECKY_HOME`
- NEVER write outside `DECKY_HOME`. settings/runtime/log survive updates, `DECKY_PLUGIN_DIR` does not.

**Persistence:** Use `SettingsManager`, NOT raw files, NOT frontend localStorage:
```python
settings.setSetting(key, value)
settings.commit()  # flush
```

**Rules of thumb:** Keep `_main()` non-blocking (spawn tasks), make RPC fast or backgrounded, avoid `root` flag unless genuinely needed (mounting, etc.)

**Compiled backends:** If you have binaries, all source must be in `backend/src`, Makefile must `mkdir -p ./out && gcc -o ./out/binary ./src/main.c`. CI only picks up `backend/out/`.

## 8. plugin.json Rules

```json
{
  "name": "Display Name",
  "author": "Your Name",
  "api_version": 1,
  "flags": ["debug"],
  "publish": {
    "description": "Short description for store",
    "tags": ["utility", "audio", "etc"],
    "image": "https://.../image.png"
  }
}
```

- `api_version: 1` = modern @decky/api. Required for new plugins
- `debug` enables auto-reload in dev — MUST be removed before store submission
- `root` only if you need elevated FS access — requires justification
- `publish.image` must be PNG
- `package.json` version must be bumped before every release

## 9. Packaging & Distribution

- Store submission is via https://github.com/SteamDeckHomebrew/decky-plugin-database — `$submit GITHUB_USER/REPO RELEASE_TAG` in Discord
- Include LICENSE file — standard is your license at top, original template license at bottom
- Zip name format: `pluginname-v1.0.0.zip` recommended
- Test install from ZIP: Decky > Settings > Developer > Install Plugin from ZIP File

## 10. Checklists

**New feature:**
1. Python method on Plugin class in main.py
2. Typed callable in src/services/api.ts
3. UI component using @decky/ui
4. `pnpm run build` + deploy to Deck + test on real hardware

**Pre-publish:**
- [ ] version bumped in package.json
- [ ] LICENSE present
- [ ] `pnpm run build` succeeds
- [ ] Binaries in backend/out/ if applicable
- [ ] No debug/root flags unless needed
- [ ] Store image is PNG
- [ ] Tested on real Deck, not just mock
- [ ] `pnpm update @decky/ui --latest` if build errors after Steam update

## 11. Common Commands

```bash
pnpm i                         # install deps
pnpm run build                 # build frontend
pnpm run watch                 # watch frontend
pnpm update @decky/ui --latest # fix stale Decky UI types
./cli/decky plugin build .     # build zip
decky plugin deploy -i <IP>    # deploy to Deck
```

## 12. DO and DON'T

**DO:**
- Use template as base, don't write rollup config from scratch
- Use callable() for all frontend<->backend comms
- Use SettingsManager for persistence
- Use decky.logger
- Test on real Deck with Developer Mode

**DON'T:**
- Add react/react-dom as dependencies
- Use old ServerAPI.callPluginMethod (legacy)
- Write outside DECKY_HOME
- Leave debug flag in for store submission
- Put binaries outside backend/out
- Use frontend localStorage for settings

## 13. References

- Wiki Getting Started: https://wiki.deckbrew.xyz/en/plugin-dev/getting-started
- Env Vars: https://wiki.deckbrew.xyz/en/plugin-dev/env-vars
- Submitting: https://wiki.deckbrew.xyz/en/plugin-dev/submitting-plugins
- Frontend Lib: https://github.com/SteamDeckHomebrew/decky-frontend-lib
- Decky Loader: https://github.com/SteamDeckHomebrew/decky-loader
- Discord: https://deckbrew.xyz/discord

---
Generated from official template README + best practices from community (xray-decky SKILL.md, decky-plugin-database).
