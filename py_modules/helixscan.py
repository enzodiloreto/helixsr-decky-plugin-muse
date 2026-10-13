# Copyright 2026 lonewolf0622. Licensed under the Apache License, Version 2.0.
#
# Game scanning and install/uninstall for the HelixSR Decky plugin, taken from
# HelixSR's own setup/helixsr_install.py (the file helixsr-setup.sh runs after
# building the network). Kept as close to the reference as possible so behaviour
# matches the official setup; adaptations for the plugin are marked ADAPTED.
#
# Adaptations vs the reference:
# - no CLI, no questions, no manual-install folder: only scan/install/uninstall.
# - Game/games take the HelixSR folder (`here`) explicitly (the reference uses
#   paths relative to its own file).
# - steam_roots() honours DECKY_USER_HOME so the Deck's user is found even if
#   the plugin host's home differs.
# - games() isolates failures per game: one unreadable game folder can no longer
#   abort (or hang) the whole scan; skipped games are reported.
# - say() collects messages for the UI instead of printing.
"""helixscan.py: HelixSR game scan + install/uninstall (from helixsr_install.py)."""
import json
import os
import re
import shutil
import struct
from pathlib import Path

DLL = 'amd_fidelityfx_dx12.dll'
DLL_SR = 'amd_fidelityfx_upscaler_dx12.dll'
NAMES = (DLL_SR, DLL)                     # a game's FSR 3.1 DLLs, the upscaler one first
OWN = 'helixsr.dll'                       # HelixSR in the HelixSR folder for OptiScaler's FfxDx12Path, and a second copy
OWN_SR = 'helixsr_upscaler.dll'           # for FfxDx12SRPath: OptiScaler hooks the upscaler module, so the two must be
                                          # different files (one file in both roles hangs the game when the upscaler starts)
NETWORK = ('helixsr_weights.bin', 'helixsr_kernels.pak')
FOLDER = 'HelixSR'
MARKER = 'helixsr-install.json'
BACKUP = 'backup'
OPTI_NAMES = ('dxgi.dll', 'winmm.dll', 'version.dll', 'dbghelp.dll', 'd3d12.dll', 'wininet.dll', 'winhttp.dll',
              'OptiScaler.asi')
OPTI_EXTRA = ('fakenvapi.dll', 'fakenvapi.ini')   # OptiScaler loads fakenvapi.dll on non-NVIDIA GPUs (games that check nvapi)
AMD_FSR = 'amd_fidelityfx_upscaler_dx12.amd.dll'   # AMD's FSR (folder "amd-fsr"): HelixSR lists it after itself
AMD_FG = 'amd_fidelityfx_framegeneration_dx12.dll'   # AMD's frame generation (folder "amd-fsr"): HelixSR has none
MY_FSR = 'my-fsr'                                 # in a game's HelixSR folder: an FSR DLL put there is listed after HelixSR
INI = 'helixsr.ini'                               # HelixSR's settings, next to HelixSR's DLL in each route
# Made for the user when a game has none: every setting commented out, so HelixSR behaves exactly as without the file.
# Windows line endings (Notepad). Only settings the README documents.
INI_TEMPLATE = '''\
; HelixSR settings. HelixSR reads this file next to its DLL.
; To change a setting, remove the ; at the start of its line, edit the value, save, and start the game again.
; Lines that start with ; are ignored: HelixSR uses its default for them.
; Keep the file name helixsr.ini (not helixsr.ini.txt).

[Sharpening]
; Sharpener: off, nvidia (NVIDIA's sharpening filter) or rcas (AMD FSR's). Default: off (as DLSS, which does not sharpen)
;Sharpener = nvidia
; Sharpness: 0.0 to 1.0, used when a sharpener is on. 0.5 is NVIDIA's default
;Sharpness = 0.5
; UseGameSlider: true follows the game's sharpness slider, if it has one. Default: false
;UseGameSlider = false

[Compatibility]
; WaveSize: default, 64 or 32. Only for RX 7000 / 9000 and Ryzen with RDNA 3 / 3.5 graphics in games installed the
; OptiScaler way, if helixsr.log says "network kernels wave32": set WaveSize = 64. Leave it alone on other GPUs.
;WaveSize = default
'''.replace('\n', '\r\n')
LOADER_DIR = 'dlss-loader'                        # the HelixSR DLSS loader (experimental route "dlss")
LOADER = 'dxgi.dll'
CORE = 'nvngx.dll'                                # HelixSR next to the .exe under the DLSS core's name
OLD_FILES = ('nvngx.dll', DLL, DLL_SR)            # left in the HelixSR folder by HelixSR 1.5 / 1.6 / early 1.7
SKIP_EXE = re.compile(r'unins|setup|redist|crash|launcher|vc_?redist|dxsetup|easyanticheat|beservice|dotnet|'
                      r'helper|report|prereq|install|7z|ue4prereq|cefprocess|webhelper', re.I)
INPUTS = {'nvngx_dlss.dll': 'DLSS', 'libxess.dll': 'XeSS', 'ffx_fsr2_api_x64.dll': 'FSR 2',
          'ffx_fsr2_api_dx12_x64.dll': 'FSR 2'}

# ADAPTED: messages are collected for the plugin UI instead of printed.
_log: list[str] = []


def say(msg):
    _log.append(str(msg))


def take_log() -> list[str]:
    out = list(_log)
    del _log[:]
    return out


def is_helixsr(path):
    try:
        with open(path, 'rb') as f:
            return re.search(rb'HelixSR \d+\.\d+\.\d+', f.read(4 << 20)) is not None
    except OSError:
        return False


def is_loader(path):
    try:
        with open(path, 'rb') as f:
            return b'HelixSR DLSS loader' in f.read(1 << 20)
    except OSError:
        return False


def is_optiscaler(path):
    try:
        with open(path, 'rb') as f:
            return b'OptiScaler' in f.read(32 << 20)
    except OSError:
        return False


def version_of(dll):
    m = re.search(rb'\x00HelixSR (\d+\.\d+\.\d+)\x00', Path(dll).read_bytes()[:4 << 20])
    return m.group(1).decode() if m else '?'


def copy_checked(src, dst):
    shutil.copyfile(src, dst)
    if Path(dst).read_bytes() != Path(src).read_bytes():
        raise SystemExit(f'{dst} differs from the file just copied: something on this PC changes new files there '
                         '(antivirus or a synced folder). Close the game and try again.')


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_fsr_source = {}


def fsr_source(here):
    """The FSR DLL listed next to HelixSR: one the user put into my-fsr, else AMD's that comes with HelixSR."""
    if here not in _fsr_source:
        _fsr_source[here] = find_fsr_source(here)
    return _fsr_source[here]


def find_fsr_source(here):
    mine = sorted(p for p in (here / 'my-fsr').glob('*') if p.suffix.lower() == '.dll') if (here / 'my-fsr').is_dir() else []
    for p in mine:
        if b'ffxCreateContext' in p.read_bytes():
            return p
        say(f'note: {p.name} in my-fsr is not an FSR (FidelityFX) DLL; ignored')
    return here / 'amd-fsr' / DLL_SR


def read_marker(d):
    """The install record in d ({} if there is none or it cannot be read)."""
    try:
        return json.loads((d / MARKER).read_text())
    except (OSError, ValueError):
        return {}


def make_ini(d, made):
    """A commented helixsr.ini in d if there is none. Returns the sha256 of the file HelixSR made (to remove it on
    uninstall only while unchanged), or `made` (the earlier one, None for a file the user made) if a file is there."""
    p = d / INI
    if p.exists():
        return made
    with open(p, 'w', encoding='ascii', newline='') as f:
        f.write(INI_TEMPLATE)
    return sha(p)


def remove_ini(d, made):
    """Removes the helixsr.ini HelixSR made in d, if unchanged. One the user made or edited is kept."""
    p = d / INI
    if not made or not p.exists():
        return
    if sha(p) == made:
        p.unlink()
    else:
        say(f'  your settings in {p} were left there (edited); delete the file if you no longer need it')


def make_my_fsr(folder):
    (folder / MY_FSR).mkdir(exist_ok=True)
    (folder / MY_FSR / 'README.txt').write_text(MY_FSR_README, newline='\r\n' if os.name != 'nt' else None)


MY_FSR_README = '''Put an FSR upscaler DLL into this folder (for example FSR 4: amd_fidelityfx_upscaler_dx12.dll, any
file name) and start the game again. Only for this game.

In the game press Insert (OptiScaler's menu), then Upscaling tab > Upscalers > FFX Upscaler, and pick it there:
it is listed by its version, for example "FSR 4.1.1". HelixSR stays the default ("FSR HelixSR (3.1.5)").
FSR 4 is listed only if the DLL supports your GPU.

To go back: delete the DLL from this folder.
'''


def remove_my_fsr(folder):
    """Removes the game's my-fsr folder unless the user put files there; True if it was kept."""
    d = folder / MY_FSR
    if (d / 'README.txt').exists():
        (d / 'README.txt').unlink()
    try:
        d.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        return True
    return False


def has_embedded_network(path):
    """True when the DLL carries the network (the setup appends it: one-file DLL)."""
    try:
        with open(path, 'rb') as f:
            f.seek(-32, os.SEEK_END)
            return f.read(8) == b'HXSRNET1'
    except OSError:
        return False


def network_ready(here):
    return is_helixsr(here / DLL) and (has_embedded_network(here / DLL) or all((here / n).exists() for n in NETWORK))


def copy_network(here, folder):
    """Separate network files only when the DLL does not carry the network; otherwise earlier copies are removed (the
    DLL reads its own copy first)."""
    for n in NETWORK:
        if has_embedded_network(here / DLL):
            if (folder / n).exists():
                (folder / n).unlink()
        else:
            copy_checked(here / n, folder / n)


def copy_helixsr(here, folder, names):
    """HelixSR's DLL under the given names (and the network files if it does not carry the network) into folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        copy_checked(here / DLL, folder / n)
    copy_network(here, folder)


# --- finding games ---------------------------------------------------------------------------------------------------

def steam_roots():
    roots = []
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
                roots.append(Path(winreg.QueryValueEx(k, 'SteamPath')[0]))
        except OSError:
            pass
        roots.append(Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Steam')
    else:
        # ADAPTED: DECKY_USER_HOME first so the Deck's user is found even if
        # the plugin host process runs with a different home.
        h = Path(os.environ.get('DECKY_USER_HOME') or Path.home())
        roots += [h / '.local/share/Steam', h / '.steam/steam', h / '.steam/root',
                  h / '.var/app/com.valvesoftware.Steam/.local/share/Steam']
    return roots


def steam_libraries():
    libs, seen = [], set()
    for root in steam_roots():
        vdf = root / 'steamapps' / 'libraryfolders.vdf'
        paths = [root]
        if vdf.exists():
            text = vdf.read_text(errors='replace')
            paths += [Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
        for p in paths:
            common = p / 'steamapps' / 'common'
            try:
                key = common.resolve()
            except OSError:
                continue
            if common.is_dir() and key not in seen:
                seen.add(key)
                libs.append(common)
    return libs


def walk(folder, depth):
    """(directory, file names) pairs under folder, at most depth levels deep, without following links."""
    out, todo = [], [(Path(folder), 0)]
    while todo:
        d, n = todo.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        out.append((d, [e.name for e in entries if e.is_file(follow_symlinks=False) or e.is_symlink()]))
        if n < depth:
            todo += [(Path(e.path), n + 1) for e in entries if e.is_dir(follow_symlinks=False) and e.name != FOLDER]
    return out


def exe_folder(tree):
    """The folder of the game's main .exe: where OptiScaler has to go."""
    for d, names in tree:   # a folder that already has OptiScaler (or HelixSR) wins
        if 'OptiScaler.ini' in names or (d / FOLDER / MARKER).exists():
            return d
    best, size = None, -1
    for d, names in tree:
        for n in names:
            if not n.lower().endswith('.exe') or SKIP_EXE.search(n):
                continue
            s = (d / n).stat().st_size if (d / n).exists() else 0
            if n.lower().endswith('-win64-shipping.exe'):
                s += 1 << 40   # Unreal Engine: always the game itself
            if s > size:
                best, size = d, s
    return best


def original(target):
    return target.with_name(target.stem + '.original.dll')


class Game:
    """route 'optiscaler' (target = folder of the .exe) or 'direct' (targets = the game's FSR 3.1 DLLs)."""

    # ADAPTED: `here` (the HelixSR version folder) is passed explicitly; the
    # reference derives it from its own file location.
    def __init__(self, name, folder, tree, here):
        self.name, self.folder = name, folder
        low = {d: {n.lower(): n for n in names} for d, names in tree}
        self.inputs = sorted({INPUTS[n] for d in low for n in low[d] if n in INPUTS}, key=['DLSS', 'XeSS', 'FSR 2'].index)
        fsr = [d / low[d][n] for d in low for n in NAMES if n in low[d]]
        up = [p for p in fsr if p.name.lower() == DLL_SR]
        self.fsr_targets = up or [p for p in fsr if p.name.lower() == DLL]
        direct_installed = any(original(t).exists() and is_helixsr(t) for t in self.fsr_targets)
        self.target = exe_folder(tree)
        # FSR 3.1 games get the drop-in (no OptiScaler), also when they have DLSS, unless OptiScaler was picked for the
        # game (answer o at the setup's question for the game, kept in the install record); OptiScaler for the others
        marker = self.target / FOLDER / MARKER if self.target else None
        try:
            rec = json.loads(marker.read_text()) if marker and marker.exists() else {}
        except (OSError, ValueError):
            rec = {}
        self.dlss_installed = rec.get('route') == 'dlss'
        self.opti_installed = bool(marker and marker.exists()) and not self.dlss_installed
        self.chosen = rec.get('chosen') == 'optiscaler' and self.opti_installed
        self.can_opti = bool(self.fsr_targets and self.inputs and self.target)   # either way works
        self.can_dlss = bool('DLSS' in self.inputs and self.target and (here / LOADER_DIR / LOADER).exists())
        if self.dlss_installed:
            self.route = 'dlss'
        elif self.fsr_targets and not self.chosen:
            self.route = 'direct'
        else:
            self.route = 'optiscaler' if self.inputs and self.target else None

    def installed(self):
        if self.route == 'dlss':
            return self.dlss_installed
        if self.route == 'direct':
            return self.opti_installed or any(original(t).exists() and is_helixsr(t) for t in self.fsr_targets)
        return self.route == 'optiscaler' and (self.target / FOLDER / MARKER).exists()

    def label(self):
        if self.route == 'dlss':
            return 'DLSS (HelixSR as the game\'s DLSS, experimental: select DLSS in the game)'
        if self.route == 'direct':
            return 'FSR 3.1 (HelixSR replaces the game\'s FSR DLL: select AMD FSR in the game)'
        return ' / '.join(self.inputs) + ' (through OptiScaler: select DLSS in the game)'


def other_stores():
    """(name, folder) of games installed by other stores: Epic, Xbox / Game Pass, GOG (Windows); Heroic, Lutris (Linux)."""
    found = []
    def add(name, folder):
        try:
            p = Path(folder)
            if p.is_dir():
                found.append((name or p.name, p))
        except (OSError, ValueError):
            pass
    if os.name == 'nt':
        # Epic Games Launcher: one JSON manifest per installed game
        man = Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests'
        for m in (man.glob('*.item') if man.is_dir() else []):
            try:
                j = json.loads(m.read_text(errors='replace'))
                add(j.get('DisplayName'), j.get('InstallLocation', ''))
            except (OSError, ValueError):
                pass
        # Xbox app / Game Pass: <drive>:\XboxGames\<game> (files under Content), and moddable WindowsApps installs
        import string
        for letter in string.ascii_uppercase:
            xg = Path(f'{letter}:\\XboxGames')
            try:
                if xg.is_dir():
                    for d in xg.iterdir():
                        add(d.name, d / 'Content' if (d / 'Content').is_dir() else d)
            except OSError:
                pass
        mwa = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'ModifiableWindowsApps'
        try:
            for d in (mwa.iterdir() if mwa.is_dir() else []):
                add(d.name, d)
        except OSError:
            pass
        # GOG Galaxy: registry entries per game, and its default folder
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\GOG.com\Games') as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as g:
                            add(winreg.QueryValueEx(g, 'gameName')[0], winreg.QueryValueEx(g, 'path')[0])
                    except OSError:
                        pass
        except OSError:
            pass
        gog = Path(r'C:\GOG Games')
        try:
            for d in (gog.iterdir() if gog.is_dir() else []):
                add(d.name, d)
        except OSError:
            pass
    else:
        # ADAPTED: same DECKY_USER_HOME override as steam_roots().
        h = Path(os.environ.get('DECKY_USER_HOME') or Path.home())
        for cfg in (h / '.config/heroic', h / '.var/app/com.heroicgameslauncher.hgl/config/heroic'):
            leg = cfg / 'legendaryConfig/legendary/installed.json'   # Heroic's Epic games
            try:
                for v in (json.loads(leg.read_text(errors='replace')).values() if leg.exists() else []):
                    add(v.get('title'), v.get('install_path', ''))
            except (OSError, ValueError, AttributeError):
                pass
            gi = cfg / 'gog_store/installed.json'                    # Heroic's GOG games
            try:
                for v in (json.loads(gi.read_text(errors='replace')).get('installed', []) if gi.exists() else []):
                    add(None, v.get('install_path', ''))
            except (OSError, ValueError, AttributeError):
                pass
        lutris = h / 'Games'                                         # Lutris' default install folder
        try:
            for d in (lutris.iterdir() if lutris.is_dir() else []):
                add(d.name, d)
        except OSError:
            pass
    return found


def games(here, extra=()):
    """All compatible games, in the reference's order. ADAPTED: returns
    (games, skipped) where skipped counts game folders that raised while
    being examined, so one bad folder cannot abort the whole scan."""
    out, skipped = [], 0
    for common in steam_libraries():
        try:
            entries = sorted(common.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for d in entries:
            if not d.is_dir():
                continue
            try:
                g = Game(d.name, d, walk(d, 7), here)
            except Exception:
                skipped += 1
                continue
            if g.route:
                out.append(g)
    for name, d in other_stores():   # games from other stores (skipped when already listed)
        try:
            d = d.resolve()
        except OSError:
            continue
        if any(overlaps(g.folder, d) for g in out):
            continue
        try:
            g = Game(name, d, walk(d, 7), here)
        except Exception:
            skipped += 1
            continue
        if g.route:
            out.append(g)
    extra_games, extra_skipped = games_from(extra, here, out)
    return out + extra_games, skipped + extra_skipped


def overlaps(a, b):
    a, b = Path(a), Path(b)
    return a == b or a in b.parents or b in a.parents


def games_from(folders, here, listed=()):
    """Games in folders given by hand. ADAPTED: `here` passed explicitly."""
    out, skipped = [], 0
    for f in folders:
        d = Path(clean_path(f)).expanduser()
        if not d.is_dir():
            say(f'not a folder: {d}')
            continue
        d = d.resolve()
        same = next((g for g in list(listed) + out if overlaps(g.folder, d)), None)
        if same:
            say(f'{d} is {same.name}, already in the list')
            continue
        try:
            g = Game(d.name, d, walk(d, 7), here)
        except Exception:
            skipped += 1
            continue
        if not g.fsr_targets:
            up = d
            for _ in range(4):
                if up.parent == up:
                    break
                up = up.parent
                try:
                    h = Game(up.name, up, walk(up, 7), here)
                except Exception:
                    skipped += 1
                    break
                if h.fsr_targets and h.target and overlaps(h.target, d):
                    g = h
                    break
        if g.route:
            out.append(g)
        else:
            say(f'no DLSS, XeSS or FSR in {d}')
    return out, skipped


def clean_path(f):
    """A path as typed, pasted or dragged into a terminal: quotes, file:// URLs (%20), backslash-escaped spaces, a file
    inside the game folder (its folder is used)."""
    from urllib.parse import unquote
    f = f.strip().strip('"').strip("'").strip()
    if f.lower().startswith('file://'):
        f = unquote(f[7:])
    if os.name != 'nt':
        f = re.sub(r'\\(.)', r'\1', f)
    p = Path(f).expanduser()
    return str(p.parent if p.is_file() else p)


# --- OptiScaler.ini --------------------------------------------------------------------------------------------------

def key_re(key):
    # one "key = value" line; works with Windows (CRLF) and Linux line endings
    return re.compile(r'(?m)^(?P<key>%s)[ \t]*=[ \t]*(?P<val>[^\r\n]*?)[ \t]*(?=\r?$)' % re.escape(key))


def set_value(text, key, value, section=None):
    """Sets key = value. A missing key is added at the start of [section]."""
    found = list(key_re(key).finditer(text))
    if len(found) > 1:
        raise SystemExit(f'OptiScaler.ini: more than one "{key} =" line')
    if found:
        return key_re(key).sub(lambda m: f'{m.group("key")} = {value}', text, count=1)
    m = re.search(r'(?m)^\[%s\][ \t]*(\r?\n)' % re.escape(section or ''), text) if section else None
    if not m:
        raise SystemExit(f'OptiScaler.ini: no "{key} =" line' + (f' and no [{section}] section' if section else ''))
    return text[:m.end()] + f'{key} = {value}' + m.group(1) + text[m.end():]


def win_path(path):
    """The path as OptiScaler wants it: a Windows path (under Proton the Linux root is Z:)."""
    p = str(path.resolve())
    return p if os.name == 'nt' else 'Z:' + p.replace('/', '\\')


def read_ini(p):
    with open(p, encoding='utf-8', errors='surrogateescape', newline='') as f:
        return f.read()


def write_ini(p, text):
    with open(p, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write(text)


def point_at_helixsr(text, folder):
    text = set_value(text, 'Dx12Upscaler', 'fsr31', 'Upscalers')
    text = set_value(text, 'FfxDx12Path', win_path(folder / OWN), 'Libraries')
    text = set_value(text, 'FfxDx12SRPath', win_path(folder / OWN_SR), 'Libraries')
    return set_value(text, 'FfxDx12FGPath', win_path(folder / AMD_FG), 'Libraries')


# --- route 1: OptiScaler ---------------------------------------------------------------------------------------------

def move_to_backup(t, name, rec):
    src = t / name
    if not (src.exists() or src.is_symlink()) or name in rec['moved']:
        return
    dst = t / FOLDER / BACKUP / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    rec['moved'].append(name)


def install_optiscaler(t, here):
    folder = t / FOLDER
    marker = folder / MARKER
    rec = json.loads(marker.read_text()) if marker.exists() else {'moved': [], 'created': []}
    if 'previous' in rec:   # HelixSR 1.5: it only changed two OptiScaler.ini lines; undo them first
        text = read_ini(t / 'OptiScaler.ini')
        for key, value in rec['previous'].items():
            text = set_value(text, key, value)
        write_ini(t / 'OptiScaler.ini', text)
        rec = {'moved': [], 'created': []}
    rec.setdefault('created', [])
    folder.mkdir(exist_ok=True)
    opti = rec.get('optiscaler') or next((n for n in OPTI_NAMES if (t / n).exists() and is_optiscaler(t / n)), 'dxgi.dll')
    if 'optiscaler' not in rec:   # first install: keep what the game had
        move_to_backup(t, opti, rec)
        if (t / 'OptiScaler.ini').exists():
            (folder / BACKUP).mkdir(parents=True, exist_ok=True)
            shutil.copy2(t / 'OptiScaler.ini', folder / BACKUP / 'OptiScaler.ini')
            rec['ini'] = 'edited'
        else:
            shutil.copyfile(here / 'optiscaler' / 'OptiScaler.ini', t / 'OptiScaler.ini')
            rec['ini'] = 'created'
        rec['optiscaler'] = opti
    for n in OPTI_EXTRA:
        if not (t / n).exists():
            shutil.copyfile(here / 'optiscaler' / n, t / n)
            if n not in rec['created']:
                rec['created'].append(n)
    if (t / opti).is_symlink():
        (t / opti).unlink()
    copy_checked(here / 'optiscaler' / 'OptiScaler.dll', t / opti)
    for n in OLD_FILES:   # only HelixSR's own older copies
        if (folder / n).exists() and is_helixsr(folder / n):
            (folder / n).unlink()
    copy_helixsr(here, folder, (OWN, OWN_SR))
    make_my_fsr(folder)
    src, dst = fsr_source(here), folder / AMD_FSR
    if dst.exists() and rec.get('amd_fsr') and sha(dst) != rec['amd_fsr']:
        say(f'  keeping your {AMD_FSR} in {FOLDER} (replaced by hand)')
    else:
        copy_checked(src, dst)
        rec['amd_fsr'] = sha(dst)
    src, dst = here / 'amd-fsr' / AMD_FG, folder / AMD_FG   # FSR frame generation (OptiScaler's or the game's)
    if dst.exists() and rec.get('amd_fg') and sha(dst) != rec['amd_fg']:
        say(f'  keeping your {AMD_FG} in {FOLDER} (replaced by hand)')
    else:
        copy_checked(src, dst)
        rec['amd_fg'] = sha(dst)
    if not (t / 'OptiScaler.ini').exists():   # deleted or moved by hand since the last install: a fresh one
        shutil.copyfile(here / 'optiscaler' / 'OptiScaler.ini', t / 'OptiScaler.ini')
        say('  OptiScaler.ini was missing next to the game\'s .exe; made a new one')
    write_ini(t / 'OptiScaler.ini', point_at_helixsr(read_ini(t / 'OptiScaler.ini'), folder))
    rec['ini_made'] = make_ini(folder, rec.get('ini_made'))
    rec['helixsr'] = version_of(here / DLL)
    rec['route'] = 'optiscaler'
    marker.write_text(json.dumps(rec, indent=1) + '\n')
    say(f'  installed in {t} (OptiScaler as {opti}' + (f'; the previous {opti} is in {FOLDER}/{BACKUP})' if opti in rec['moved'] else ')'))


def uninstall_optiscaler(t):
    folder = t / FOLDER
    marker = folder / MARKER
    if not marker.exists():
        return
    rec = json.loads(marker.read_text())
    opti = rec.get('optiscaler', 'dxgi.dll')
    if (t / opti).exists() or (t / opti).is_symlink():
        (t / opti).unlink()
    if rec.get('ini') == 'created':
        for n in ('OptiScaler.ini', 'OptiScaler.log'):
            if (t / n).exists():
                (t / n).unlink()
    elif (folder / BACKUP / 'OptiScaler.ini').exists():
        os.replace(folder / BACKUP / 'OptiScaler.ini', t / 'OptiScaler.ini')
    for n in rec.get('created', []):
        if (t / n).exists():
            (t / n).unlink()
    for name in rec['moved']:
        src = folder / BACKUP / name
        if src.exists() or src.is_symlink():
            os.replace(src, t / name)
    for n in (OWN, OWN_SR, AMD_FSR, AMD_FG, MARKER, 'helixsr.log') + NETWORK + OLD_FILES:
        if (folder / n).exists() and (n not in OLD_FILES or is_helixsr(folder / n)):
            (folder / n).unlink()
    remove_ini(folder, rec.get('ini_made'))
    kept = remove_my_fsr(folder)
    for d in (folder / BACKUP, folder):
        try:
            d.rmdir()
        except OSError:
            pass
    say(f'  removed from {t} (everything put back as it was)')
    if kept:
        say(f'  your files in {folder / MY_FSR} were left there')


# --- route 2: the game's FSR 3.1 DLL ---------------------------------------------------------------------------------

def install_direct(g, here):
    for t in g.fsr_targets:
        orig = original(t)
        if is_helixsr(t):
            if not orig.exists():
                say(f'  {t}: HelixSR without a backup of the game\'s file; left as it is')
                continue
            copy_checked(here / DLL, t)   # update
        else:
            if orig.exists():
                say(f'  {t}: {orig.name} already exists and {t.name} is not HelixSR; left as it is')
                continue
            os.replace(t, orig)
            copy_checked(here / DLL, t)
        copy_network(here, t.parent)
        made = read_marker(t.parent).get('ini_made')
        (t.parent / MARKER).write_text(json.dumps({'helixsr': version_of(here / DLL), 'files': [t.name],
                                                   'route': 'direct', 'ini_made': make_ini(t.parent, made)},
                                                  indent=1) + '\n')
        say(f'  installed {t} (the game\'s file kept as {orig.name})')


def uninstall_direct(g):
    for t in g.fsr_targets:
        orig = original(t)
        if not orig.exists():
            continue
        if t.exists() and not is_helixsr(t):
            say(f'  {t} is not HelixSR (the game may have been updated); {orig.name} left in place')
            continue
        if t.exists():
            t.unlink()
        os.replace(orig, t)
        remove_ini(t.parent, read_marker(t.parent).get('ini_made'))
        for extra in (MARKER, 'helixsr.log') + NETWORK:
            if (t.parent / extra).exists():
                (t.parent / extra).unlink()
        say(f'  restored the game\'s {t}')


# --- route 3 (experimental): the HelixSR DLSS loader ------------------------------------------------------------------

def install_dlss(t, here):
    """dxgi.dll (the loader) and HelixSR as nvngx.dll next to the game's .exe; files of the same names that are not
    HelixSR's go to HelixSR/backup first."""
    folder = t / FOLDER
    marker = folder / MARKER
    rec = json.loads(marker.read_text()) if marker.exists() else {'moved': [], 'created': []}
    rec.setdefault('moved', [])
    folder.mkdir(exist_ok=True)
    for name, ours in ((LOADER, is_loader), (CORE, is_helixsr)):
        if (t / name).exists() and not ours(t / name):
            move_to_backup(t, name, rec)
    copy_checked(here / LOADER_DIR / LOADER, t / LOADER)
    copy_checked(here / DLL, t / CORE)
    rec.update({'helixsr': version_of(here / DLL), 'route': 'dlss', 'files': [LOADER, CORE],
                'ini_made': make_ini(t, rec.get('ini_made'))})
    marker.write_text(json.dumps(rec, indent=1) + '\n')
    say(f'  installed in {t} (DLSS loader as {LOADER}, HelixSR as {CORE}' +
        (f'; the game\'s previous files are in {FOLDER}/{BACKUP})' if rec['moved'] else ')'))
    if (t / 'OptiScaler.ini').exists():
        say('  NOTE: an OptiScaler that HelixSR did not install is next to the game\'s .exe (OptiScaler.ini); remove it for '
            'this way (its dxgi.dll was moved to the backup if it used that name).')


def uninstall_dlss(t):
    folder = t / FOLDER
    marker = folder / MARKER
    if not marker.exists():
        return
    rec = json.loads(marker.read_text())
    for name, ours in ((LOADER, is_loader), (CORE, is_helixsr)):
        if (t / name).exists() and ours(t / name):
            (t / name).unlink()
    for name in rec.get('moved', []):
        src = folder / BACKUP / name
        if src.exists() or src.is_symlink():
            os.replace(src, t / name)
    remove_ini(t, rec.get('ini_made'))
    for n in (MARKER, 'helixsr.log'):
        if (folder / n).exists():
            (folder / n).unlink()
    for n in ('helixsr.log', 'helixsr_loader.log'):
        if (t / n).exists():
            (t / n).unlink()
    for d in (folder / BACKUP, folder):
        try:
            d.rmdir()
        except OSError:
            pass
    say(f'  removed from {t} (everything put back as it was)')


def install(g, here):
    if g.route != 'dlss' and g.dlss_installed:   # switching away from the DLSS loader
        say('  removing the DLSS loader first')
        uninstall_dlss(g.target)
        g.dlss_installed = False
    if g.route == 'dlss':
        if g.opti_installed:
            say('  removing OptiScaler first (the DLSS loader replaces it)')
            uninstall_optiscaler(g.target)
            g.opti_installed = False
        if any(original(t).exists() and is_helixsr(t) for t in g.fsr_targets):
            say('  removing the FSR drop-in first (the DLSS loader was picked for this game)')
            uninstall_direct(g)
        install_dlss(g.target, here)
        return
    if g.route == 'direct' and g.opti_installed:   # an earlier version used OptiScaler here: the drop-in replaces it
        say('  removing the OptiScaler install of an earlier version (this game has FSR 3.1: HelixSR replaces its FSR DLL)')
        uninstall_optiscaler(g.target)
        g.opti_installed = False
    if g.route == 'optiscaler' and any(original(t).exists() and is_helixsr(t) for t in g.fsr_targets):
        say('  removing the drop-in first (OptiScaler was picked for this game)')
        uninstall_direct(g)
    install_optiscaler(g.target, here) if g.route == 'optiscaler' else install_direct(g, here)
    if g.route == 'optiscaler' and g.chosen:   # remembered: updates keep the OptiScaler way
        m = g.target / FOLDER / MARKER
        rec = json.loads(m.read_text())
        rec['chosen'] = 'optiscaler'
        m.write_text(json.dumps(rec, indent=1) + '\n')
    if g.route == 'direct' and g.target and (g.target / 'OptiScaler.ini').exists():
        say('  NOTE: an OptiScaler that HelixSR did not install is next to the game\'s .exe (OptiScaler.ini); it can take '
            'over the game\'s FSR calls. For the drop-in, remove it (its dxgi.dll / winmm.dll etc. and OptiScaler.ini).')


def uninstall(g):
    if g.dlss_installed:
        uninstall_dlss(g.target)
        g.dlss_installed = False
        return
    if g.route == 'direct' and g.opti_installed:
        uninstall_optiscaler(g.target)
        g.opti_installed = False
    uninstall_optiscaler(g.target) if g.route == 'optiscaler' else uninstall_direct(g)
