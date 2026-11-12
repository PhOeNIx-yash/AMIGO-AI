// What Sidekick may do on its own, what needs a yes from the user first, and what it never does.
// Pure functions so every rule is tested (test/safety.test.mjs). When unsure, ask; never guess "safe".
import fs from 'node:fs';
import nodePath from 'node:path';
// Rules work on forward-slash paths everywhere, so a Windows path (C:\Users\ken\.ssh) meets the same checks.
const path = nodePath.posix;
const slash = (p) => String(p ?? '').replace(/\\/g, '/');
const isAbs = (p) => p.startsWith('/') || /^[a-z]:\//i.test(p);

export const SAFE = 'safe', ASK = 'ask', BLOCK = 'block';
const verdict = (level, reason = '') => ({ level, reason });

// Secrets: keys, tokens, passwords, browser cookies and the keychain. Never read, never sent anywhere.
const SECRET = [
  /(^|\/)\.ssh(\/|$)/, /(^|\/)\.gnupg(\/|$)/, /(^|\/)\.aws(\/|$)/, /(^|\/)\.azure(\/|$)/, /(^|\/)\.kube\/config$/,
  /(^|\/)\.docker\/config\.json$/, /(^|\/)\.netrc$/, /(^|\/)\.npmrc$/, /(^|\/)\.pypirc$/, /(^|\/)\.git-credentials$/,
  /(^|\/)\.config\/gh(\/|$)/, /(^|\/)\.config\/gcloud(\/|$)/, /(^|\/)\.gg\/auth\.json$/, /(^|\/)\.tauri(\/|$)/,
  /(^|\/)Library\/Keychains(\/|$)/, /(^|\/)Library\/Cookies(\/|$)/, /\/(Cookies|Login Data|Web Data)(-journal)?$/,
  /(^|\/)\.env(\.[\w-]+)?$/, /\.(pem|p12|pfx|key|keystore|jks)$/i, /(^|\/)id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$/,
  /(^|\/)\.(bash|zsh|fish)_history$/, /(^|\/)\.zsh_sessions(\/|$)/,
  /(^|\/)AppData\/(Roaming\/Microsoft\/(Credentials|Protect|Crypto)|Local\/Microsoft\/Credentials)(\/|$)/i, /\/(User Data|Profiles)\/.*\/(Cookies|Login Data|logins\.json|key4\.db)$/i,
  // Linux: GNOME's keyring and KDE's wallet, pass, the NSS certificate store and Firefox's saved logins and cookies.
  /(^|\/)\.local\/share\/(keyrings|kwalletd)(\/|$)/, /(^|\/)\.password-store(\/|$)/, /(^|\/)\.pki(\/|$)/,
  /(^|\/)\.mozilla\/.*\/(cookies\.sqlite|logins\.json|key4\.db|cert9\.db)$/,
];
// Other apps' private data and synced libraries: reading these sets off macOS privacy prompts.
const PRIVATE = [
  /(^|\/)Library\/(Containers|Group Containers|Application Support\/com\.apple\.TCC|Mail|Messages|Safari|Calendars|Photos|Accounts)(\/|$)/,
  /(^|\/)Pictures\/Photos Library\.photoslibrary(\/|$)/, /(^|\/)Music\/(Music|iTunes)(\/|$)/,
  /(^|\/)AppData\/Local\/Packages(\/|$)/i,
];
// Writing here could let something run on its own later, or break the Mac.
const NO_WRITE = [
  /(^|\/)\.(zshrc|zprofile|zshenv|zlogin|bashrc|bash_profile|profile|login|inputrc)$/, /(^|\/)\.config\/fish(\/|$)/,
  /(^|\/)Library\/(LaunchAgents|LaunchDaemons|StartupItems|Preferences|Keychains|Application Support\/com\.apple)(\/|$)/,
  /(^|\/)\.gitconfig$/, /(^|\/)\.ssh(\/|$)/, /(^|\/)\.gg(\/|$)/, /(^|\/)\.tauri(\/|$)/, /\.app(\/|$)/, /(^|\/)crontab$/,
  /\/Start Menu\/Programs\/Startup(\/|$)/i, /(^|\/)Documents\/(Windows)?PowerShell\/.*profile\.ps1$/i, /^[a-z]:\/(Windows|Program Files( \(x86\))?|ProgramData)(\/|$)/i,
  // Linux: what starts at login (autostart entries, systemd user units, session environment), app launchers and
  // D-Bus services (each names a program the desktop runs), and the X session's startup files.
  /(^|\/)\.config\/(autostart|autostart-scripts|systemd|environment\.d|plasma-workspace\/(env|shutdown))(\/|$)/,
  /(^|\/)\.local\/share\/(systemd|dbus-1|applications|kservices[56]?)(\/|$)/, /\.desktop$/i,
  /(^|\/)\.(xprofile|xinitrc|xsession|xsessionrc|pam_environment|bash_login|bash_logout)$/,
];
const SYSTEM_ROOTS = ['/System', '/usr', '/bin', '/sbin', '/etc', '/private', '/Library', '/Applications', '/var', '/opt', '/cores'];

const norm = (p, home) => {
  const s = slash(p).trim(), h = slash(home);
  const abs = s === '~' ? h : s.startsWith('~/') ? path.join(h, s.slice(2)) : isAbs(s) ? s : path.join(h, s);
  return path.normalize(abs).replace(/(.)\/$/, '$1');
};
const rel = (abs, home) => { const h = slash(home); return abs === h ? '' : abs.startsWith(h + '/') ? abs.slice(h.length + 1) : abs; };
// Where a path really leads once symlinks are followed (also a dangling link, or a path not made yet): a link in a
// harmless folder can point into ~/.ssh or a startup file.
function realOf(abs, depth = 0) {
  if (depth > 8) return abs;
  try { return slash(fs.realpathSync.native(abs)); } catch {}
  const rest = [];
  let at = abs;
  for (let i = 0; i < 64; i++) {
    try {
      const st = fs.lstatSync(at);
      if (st.isSymbolicLink()) { const t = slash(fs.readlinkSync(at)); return realOf(path.join(isAbs(t) ? t : path.join(path.dirname(at), t), ...rest), depth + 1); }
      return path.join(slash(fs.realpathSync.native(at)), ...rest);
    } catch {
      const up = path.dirname(at);
      if (up === at) return abs;
      rest.unshift(path.basename(at)); at = up;
    }
  }
  return abs;
}
// The same path and where it really leads: every rule is checked against both.
// Windows puts a drive on a /-path (/tmp → D:/tmp) and may change letter case: that's the same path, not a link.
const samePath = (a, b) => { const k = (p) => (process.platform === 'win32' ? p.replace(/^[a-z]:(?=\/)/i, '').toLowerCase() : p); return k(a) === k(b); };
const bothOf = (abs) => { const r = realOf(abs); return samePath(r, abs) ? [abs] : [abs, r]; };

// Buttons that delete, pay, send or sign out: Sidekick needs the user's yes before clicking one,
// even if it was asked to "show" it. Kept in sync with RISKY in hands/main.swift.
export const RISKY_CLICK = /(?:^|[^\p{L}])(delete|remove|erase|destroy|wipe|reset|deactivate|close (my |your )?account|cancel (my |your |the )?(subscription|plan|membership|account|order)|unsubscribe|pay|purchase|buy|place (the |your )?order|checkout|check out|confirm (payment|order|purchase|transfer)|send|transfer|withdraw|uninstall|empty trash|discard|sign out|log out|logout|format|revoke|publish|eliminar|borrar|pagar|comprar|enviar|cerrar sesi[oó]n|supprimer|effacer|payer|acheter|envoyer|se d[eé]connecter|d[eé]connexion|l[oö]schen|entfernen|bezahlen|kaufen|senden|abmelden|excluir|apagar|terminar sess[aã]o|elimina|cancella|paga|acquista|invia|esci|verwijderen|betalen|kopen|verzenden|uitloggen|afmelden|padam|hapus|bayar|beli|hantar|kirim|log keluar|nyahlanggan)(?![\p{L}])|删除|刪除|移除|清空|支付|付款|购买|購買|发送|發送|退出登录|登出|注销|削除|消去|支払|購入|送信|ログアウト|退会|삭제|결제|구매|전송|보내기|로그아웃/iu;
export const isRiskyClick = (label) => RISKY_CLICK.test(String(label ?? ''));
// Which kind of risky click a label is ("Delete post" → "delete"), so one yes can cover the same kind and no other.
export function riskyKind(label) {
  const m = RISKY_CLICK.exec(String(label ?? ''));
  return m ? (m[1] ?? m[0]).toLowerCase().replace(/\s+/g, ' ') : null;
}

/** Tool arguments as they go in the activity log: anything typed into fields or files becomes its length. */
export function redactArgs(name, args = {}) {
  const len = (t) => `[${String(t ?? '').length} chars]`;
  // Steps that aren't a list (a model sent them as one string) are logged as their size only: they can hold typed
  // text too, and reading them as a list crashed the request before the tool could say what was wrong.
  if (name === 'act_in_app') return { ...args, steps: Array.isArray(args.steps) ? args.steps.map((s) => (['type', 'set', 'fill'].includes(s?.do) ? { ...s, text: len(s.text) } : s)) : args.steps == null ? [] : `[${typeof args.steps}, ${len(typeof args.steps === 'string' ? args.steps : JSON.stringify(args.steps)).slice(1)}` };
  if (name === 'write_file') return { path: args.path, content: len(args.content) };
  return args;
}

/** read_file / list_files / search_files. Returns { level, reason, path }. */
export function checkRead(p, home, { recursive = false } = {}) {
  home = slash(home);
  const abs = norm(p, home), all = bothOf(abs), hit = (list) => all.some((a) => list.some((re) => re.test(rel(a, home)) || re.test(a)));
  if (hit(SECRET)) return { ...verdict(BLOCK, `${p} holds passwords or keys, so I never read it.`), path: abs };
  if (hit(PRIVATE)) return { ...verdict(BLOCK, `${p} is another app's private data. Reading it sets off macOS privacy prompts.`), path: abs };
  if (recursive && (abs === home || abs === '/' || /^[a-z]:$/i.test(abs) || abs === path.join(home, 'Library') || abs === path.join(home, 'AppData') || /^(\/Users|\/Volumes|\/home|[a-z]:\/Users)$/i.test(abs))) {
    return { ...verdict(BLOCK, `Searching all of ${p} reaches into other apps' data and sets off macOS privacy prompts. Pick a specific folder, or use Spotlight (mdfind) to find files by name.`), path: abs };
  }
  return { ...verdict(SAFE), path: abs };
}

/** write_file. Existing files need a yes; secrets, startup files and system folders never. */
export function checkWrite(p, home, { exists = false } = {}) {
  home = slash(home);
  const abs = norm(p, home), r = rel(abs, home), all = bothOf(abs);
  if (all.some((a) => [...SECRET, ...NO_WRITE].some((re) => re.test(rel(a, home)) || re.test(a)))) {
    return { ...verdict(BLOCK, `${p} is a protected file (keys, settings, startup scripts or an app), so I won't write to it.`), path: abs };
  }
  const realHome = realOf(home), inside = (a) => [home, realHome].some((h) => a.startsWith(h + '/')) || /^\/(private\/)?tmp\//.test(a);
  if (!all.every(inside)) {
    return { ...verdict(BLOCK, `I only write files inside your home folder, not ${p}.`), path: abs };
  }
  if (exists) return { ...verdict(ASK, `Overwrite ${r || abs}?`), path: abs };
  return { ...verdict(SAFE), path: abs };
}

// ---------- shell commands ----------

// Split into simple commands on ; && || | & and newlines, outside quotes. Returns null if quoting is unbalanced.
export function splitCommand(cmd) {
  const parts = []; let cur = '', q = null;
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i];
    if (q) { cur += c; if (c === q) q = null; else if (c === '\\' && q === '"') cur += cmd[++i] ?? ''; continue; }
    if (c === '\\') { cur += c + (cmd[++i] ?? ''); continue; }
    if (c === '"' || c === "'") { q = c; cur += c; continue; }
    // 2>&1, <&3 and &>file are redirects, not "run in the background".
    if (c === '&' && (cmd[i - 1] === '>' || cmd[i - 1] === '<' || (cmd[i + 1] === '>' && cmd[i - 1] !== '&'))) { cur += c; continue; }
    if (c === ';' || c === '\n' || c === '|' || c === '&') {
      if (cur.trim()) parts.push({ text: cur.trim(), pipeIn: false });
      cur = '';
      if ((c === '|' || c === '&') && cmd[i + 1] === c) i++;
      else if (c === '|') parts.push({ pipe: true });
      continue;
    }
    cur += c;
  }
  if (q) return null;
  if (cur.trim()) parts.push({ text: cur.trim() });
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].pipe) { if (out.length && parts[i + 1] && !parts[i + 1].pipe) parts[i + 1].pipeIn = true; continue; }
    out.push(parts[i]);
  }
  return out;
}

const words = (s) => (s.match(/"(?:\\.|[^"])*"|'[^']*'|\S+/g) ?? []).map((w) => w.replace(/^(['"])([\s\S]*)\1$/, '$2'));
// Skip env assignments and wrappers to find the program that really runs. `assigns`: it sets environment variables
// for it (PAGER=, MANPAGER=, GIT_EXTERNAL_DIFF= and the like can make a read-only program run another one).
function program(ws) {
  let i = 0, assigns = false;
  while (i < ws.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(ws[i])) { i++; assigns = true; }
  while (i < ws.length && ['command', 'builtin', 'exec', 'nohup', 'time', 'nice', 'caffeinate', 'env', 'xargs', 'stdbuf'].includes(path.basename(ws[i]))) {
    i++; while (i < ws.length && ws[i].startsWith('-')) i++;
    while (i < ws.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(ws[i])) { i++; assigns = true; }
  }
  return { name: slash(ws[i] ?? '').split('/').pop().replace(/\.(exe|com|bat|cmd)$/i, ''), args: ws.slice(i + 1), assigns };
}
// "Read-only" programs that still write a file named in their arguments, or run another program they're given.
// Said why, or null when the arguments are harmless.
function sideEffect(name, args) {
  const plain = args.filter((a) => !a.startsWith('-')), flag = (re) => args.some((a) => re.test(a));
  if (name === 'sort' && flag(/^(-o.*|--output(=.*)?)$/)) return '`sort -o` writes a file.';
  if (name === 'uniq' && plain.length >= 2) return '`uniq` with two files writes the second one.';
  if (name === 'xxd' && (flag(/^-r/) || plain.length >= 2)) return '`xxd` here writes a file.';
  if (name === 'tree' && flag(/^-o/)) return '`tree -o` writes a file.';
  if (name === 'file' && flag(/^-C$/)) return '`file -C` writes a file.';
  if ((name === 'less' || name === 'more') && flag(/^(-[oO].*|--log-file(=.*)?|--LOG-FILE(=.*)?)$/)) return '`less` here writes a log file.';
  if (name === 'man' && flag(/^(-[PHt].*|--pager(=.*)?|--html(=.*)?)$/)) return '`man` here runs another program.';
  if ((name === 'rg' || name === 'ag') && flag(/^(--pre(-glob)?(=.*)?|--pager(=.*)?|--search-zip|-z)$/)) return `\`${name}\` here runs another program on each file.`;
  if (name === 'fd' && flag(/^(-x|-X|--exec(-batch)?(=.*)?)$/)) return '`fd` here runs a command on each file.';
  if (name === 'sysctl' && (flag(/^-w$/) || plain.some((a) => a.includes('=')))) return '`sysctl` here changes system settings.';
  if (name === 'git' && flag(/^--(output|ext-diff|textconv)(=.*)?$/)) return '`git` here writes a file or runs a diff program.';
  return null;
}

const BLOCKED_PROGRAMS = {
  sudo: 'needs admin rights', doas: 'needs admin rights', su: 'switches user', mkfs: 'erases disks', newfs_apfs: 'erases disks',
  newfs_hfs: 'erases disks', fdisk: 'edits disk partitions', gpt: 'edits disk partitions', csrutil: 'changes Mac security',
  nvram: 'changes firmware settings', bless: 'changes how the Mac boots', shutdown: 'shuts the Mac down', reboot: 'restarts the Mac',
  halt: 'shuts the Mac down', launchctl: 'installs or stops background services', crontab: 'schedules commands to run on their own',
  tccutil: 'resets privacy permissions', systemsetup: 'changes system settings', dscl: 'edits user accounts', sysadminctl: 'edits user accounts',
  passwd: 'changes passwords', chflags: 'changes protected file flags', kextload: 'loads kernel code', spctl: 'changes app security checks',
  srm: 'shreds files', shred: 'shreds files', wipefs: 'erases disks', fsck: 'repairs disks', mount: 'mounts disks', umount: 'unmounts disks',
  // Windows
  format: 'erases disks', diskpart: 'edits disks', bcdedit: 'changes how Windows boots', vssadmin: 'deletes backups', wbadmin: 'deletes backups',
  schtasks: 'schedules commands to run on their own', takeown: 'takes over protected files', runas: 'needs admin rights', cipher: 'can wipe disk space',
  sc: 'installs or stops services', netsh: 'changes network and firewall settings', setx: 'changes system settings for good',
  // Linux
  pkexec: 'needs admin rights', 'systemd-run': 'starts programs as background services', at: 'schedules commands to run on their own',
  batch: 'schedules commands to run on their own', 'kwallet-query': 'reads your saved passwords',
};
const SHELLS = new Set(['powershell', 'pwsh', 'cmd', 'sh', 'bash', 'zsh', 'fish', 'dash', 'ksh', 'csh', 'tcsh', 'python', 'python3', 'node', 'perl', 'ruby', 'php', 'osascript', 'swift', 'deno', 'bun', 'lua']);
// Read-only programs that are fine to run without asking (paths still checked).
const READ_ONLY = new Set([
  'ls', 'cd', 'pwd', 'date', 'cal', 'whoami', 'id', 'uname', 'sw_vers', 'hostname', 'uptime', 'which', 'type', 'whereis', 'echo', 'printf',
  'cat', 'head', 'tail', 'less', 'more', 'wc', 'file', 'stat', 'du', 'df', 'tree', 'basename', 'dirname', 'realpath', 'readlink',
  'grep', 'egrep', 'fgrep', 'rg', 'ag', 'find', 'fd', 'mdfind', 'mdls', 'locate', 'sort', 'uniq', 'cut', 'tr', 'column', 'jq', 'diff', 'cmp',
  'ps', 'pgrep', 'top', 'vm_stat', 'sysctl', 'system_profiler', 'ioreg', 'pmset', 'defaults', 'networksetup', 'scutil', 'ifconfig',
  'sw_vers', 'arch', 'nproc', 'env', 'printenv', 'true', 'false', 'test', 'seq', 'expr', 'bc', 'md5', 'shasum', 'cksum', 'xxd', 'od',
  'man', 'tldr', 'git', 'npm', 'node', 'python3', 'python', 'brew', 'cargo', 'rustc', 'go', 'java', 'ruby', 'pip', 'pip3',
]);
// For tools that change things depending on the subcommand, these subcommands only look.
const READ_ONLY_SUB = {
  // Only the looking forms: `git branch -D`, `git tag -d`, `git remote add` change the repo. diff, show, blame and
  // log -p can run a program a downloaded repo's own settings name (textconv), so they ask.
  git: /^(status|rev-parse|ls-files|shortlog|describe|config\s+--get\b|stash\s+list|--version|-v)\b|^log\b(?!.*\s(-p|-u|--patch|-L|--ext-diff|--textconv)\b)|^branch(\s+(-a|-r|-v|-vv|--list|--all|--remotes|--show-current|--merged|--no-merged|--contains))*\s*$|^tag(\s+(-l|--list))?(\s+[^-\s]\S*)?\s*$|^remote(\s+-v)?\s*$|^remote\s+(show|get-url)\s+\S+\s*$/,
  npm: /^(ls|list|view|info|outdated|audit|--version|-v|-h|help|config\s+get|root|prefix|bin|search|explain|why)\b/,
  brew: /^(list|ls|info|search|outdated|deps|leaves|config|doctor|--version|--prefix|home)\b/,
  cargo: /^(--version|-V|tree|metadata|search)\b/, pip: /^(list|show|freeze|--version|search)\b/, pip3: /^(list|show|freeze|--version)\b/,
  defaults: /^(read|domains|find|read-type)\b/, networksetup: /^-(list|get)/, pmset: /^-g\b/,
  node: /^(-v|--version)$/, python3: /^(-V|--version)$/, python: /^(-V|--version)$/, ruby: /^(-v|--version)$/, go: /^(version|env)\b/,
  java: /^(-version|--version)$/, rustc: /^(-V|--version)$/, env: /^$/, printenv: /^$/,
};

function checkSimple(text, pipeIn, home, cwd = home) {
  const ws = words(text);
  const prog = program(ws), args = prog.args;
  // Path checks see relative arguments from the folder a cd moved to.
  const at = (a) => (cwd !== home && !a.startsWith('-') && !/^[~/$]/.test(a) && !/^[a-z]:[\\/]/i.test(a) && !/^[a-z][a-z0-9+.-]*:\/\//i.test(a) ? path.join(cwd, slash(a)) : a);
  const pathArgs = args.map(at);
  const name = prog.name.replace(/^(mkfs|newfs|fsck)[._].*$/, '$1').toLowerCase();
  const joined = args.join(' ');
  if (!name) return verdict(SAFE);
  if (BLOCKED_PROGRAMS[name]) return verdict(BLOCK, `\`${name}\` ${BLOCKED_PROGRAMS[name]}.`);
  if (name === 'diskutil' && /\b(erase\w*|partition\w*|zero\w*|secureErase|reformat|apfs\s+(delete|erase)\w*|unmount\w*|eject)\b/i.test(joined)) return verdict(BLOCK, '`diskutil` would erase or change a disk.');
  if (name === 'dd' && /\bof=/.test(joined)) return verdict(BLOCK, '`dd` writing to a file or disk can wipe it.');
  if (name === 'security' && /\b(find-(generic|internet)-password|dump-keychain|export|delete-keychain|unlock-keychain|set-keychain|add-trusted-cert|authorizationdb)\b/.test(joined)) return verdict(BLOCK, '`security` would read or change your saved passwords and certificates.');
  if (name === 'reg' && /^(delete|add|import|restore|load|copy)\b/i.test(joined)) return verdict(BLOCK, '`reg` would change the Windows registry.');
  // Linux: the desktop's keyring, background services and the D-Bus calls that reach either.
  if (name === 'secret-tool') return verdict(BLOCK, '`secret-tool` reads or changes your saved passwords.');
  if (name === 'systemctl' && /\b(enable|disable|reenable|preset|start|stop|restart|reload|try-restart|kill|mask|unmask|link|edit|revert|set-default|isolate|daemon-reload|reboot|poweroff|halt|suspend|hibernate|set-environment|import-environment)\b/.test(joined)) return verdict(BLOCK, '`systemctl` here installs, starts or stops background services.');
  if (['gdbus', 'dbus-send', 'busctl', 'qdbus', 'qdbus6'].includes(name) && /org\.freedesktop\.(secrets|impl\.portal\.Secret)|org\.kde\.kwalletd|org\.gnome\.keyring|org\.freedesktop\.systemd1/i.test(joined)) return verdict(BLOCK, `\`${name}\` here would reach your saved passwords or background services.`);
  if ((name === 'powershell' || name === 'pwsh') && /(-e(nc(odedcommand)?)?\s|iex\b|invoke-expression|downloadstring|downloadfile|invoke-webrequest|\biwr\b|start-bitstransfer|set-executionpolicy|remove-item\b.*-recurse)/i.test(joined)) return verdict(BLOCK, 'that PowerShell command downloads and runs code, or deletes whole folders.');
  if (['rd', 'rmdir', 'del', 'erase'].includes(name) && /\/s\b/i.test(joined)) {
    const t = args.filter((a) => !a.startsWith('/'));
    if (!t.length || t.some((x) => { const a = norm(x.replace(/[\\/]\*(\.\*)?$/, ''), home); return a === home || /^[a-z]:\/?$/i.test(a) || path.dirname(a) === path.dirname(home) || /\/(Desktop|Documents|Downloads)$/i.test(a); })) return verdict(BLOCK, 'that would delete a whole folder like your home, Desktop or Documents.');
    return verdict(ASK, 'This deletes folders and everything inside them.');
  }
  if (pipeIn && SHELLS.has(name)) return verdict(BLOCK, `piping into \`${name}\` runs code that hasn't been checked (like a downloaded script).`);
  if (SHELLS.has(name) && /(^|\s)-(c|e)\b/.test(joined) && !READ_ONLY_SUB[name]?.test(joined)) {
    const inner = joined.replace(/^.*?-(c|e)\s+/, '');
    const v = classifyCommand(inner, home);
    return v.level === SAFE ? verdict(ASK) : v;
  }
  if (['eval', 'source', '.'].includes(name)) return verdict(ASK);
  if ((name === 'killall' || name === 'pkill') && /\b(WindowServer|loginwindow|launchd|kernel_task|Finder|Dock|SystemUIServer|coreaudiod)\b/i.test(joined)) return verdict(BLOCK, `stopping ${joined} would break the Mac until a restart.`);
  if (name === 'kill' && /(^|\s)(-\d+\s+)?(1|-1)$/.test(joined)) return verdict(BLOCK, '`kill` on every process would crash the Mac.');
  if (name === 'rm' || name === 'rmdir' || name === 'unlink' || name === 'trash') {
    const recursive = /(^|\s)-[a-zA-Z]*[rR]|--recursive/.test(joined);
    const targets = args.filter((a) => !a.startsWith('-'));
    const broad = targets.some((t) => {
      const abs = /[*?]/.test(t) && path.dirname(t) === '.' ? home : norm(t.replace(/\/\*$/, ''), home);
      return abs === '/' || abs === home || path.dirname(abs) === '/' || abs === path.join(home, 'Library') || abs === path.join(home, 'Desktop') || abs === path.join(home, 'Documents') || abs === path.join(home, 'Downloads') || /^\$(HOME|\{HOME\})\/?$/.test(t) || t === '..' || t === '../' || t === '~/' || t === '*';
    });
    if (broad || (recursive && targets.some((t) => t.includes('*')))) return verdict(BLOCK, '`rm` on a whole folder like your home, Desktop or Documents would delete too much.');
    return verdict(ASK, recursive ? 'This deletes folders and everything inside them.' : 'This deletes files.');
  }
  if ((name === 'chmod' || name === 'chown') && /(^|\s)-[a-zA-Z]*R/.test(joined)) {
    const t = args.filter((a) => !a.startsWith('-')).slice(1);
    if (t.some((x) => { const a = norm(x, home); return a === '/' || a === home || path.dirname(a) === '/'; })) return verdict(BLOCK, `changing permissions on all of ${t.join(' ')} could lock you out of your files.`);
  }
  if (name === 'mv' && args.filter((a) => !a.startsWith('-')).some((t) => { const a = norm(t, home); return a === home || a === '/' || path.dirname(a) === '/'; })) return verdict(BLOCK, '`mv` of your whole home folder or a system folder would break the Mac.');
  if (name === 'git' && /\bpush\b.*(\s--force\b|\s-f\b|\s\+)/.test(joined)) return verdict(ASK, 'Force-pushing can erase work on the remote.');
  if (name === 'npm' && /\b(publish|unpublish|deprecate|owner|token|adduser|login)\b/.test(joined)) return verdict(ASK);
  // Walking the whole home folder (find ~, grep -r ~, du ~) trips macOS privacy prompts for Music, Photos, iCloud and other apps.
  if (['find', 'fd', 'grep', 'egrep', 'rg', 'ag', 'du', 'tree', 'ls'].includes(name)) {
    const recursive = name === 'find' || name === 'fd' || name === 'rg' || name === 'ag' || name === 'tree' || name === 'du' || /(^|\s)-[a-zA-Z]*[rR]/.test(joined);
    for (const a of pathArgs.filter((x) => !x.startsWith('-'))) {
      if (/^\$(HOME|\{HOME\})/.test(a)) { const v = checkRead(a.replace(/^\$\{?HOME\}?/, '~'), home, { recursive }); if (v.level !== SAFE) return verdict(v.level, v.reason); continue; }
      if (!/^[~/.]/.test(a)) continue;
      const v = checkRead(a, home, { recursive });
      if (v.level !== SAFE) return verdict(v.level, v.reason);
    }
    if (name === 'find' && /\s-(delete|exec|execdir|ok|okdir|fprint\w*)\b/.test(' ' + joined)) return verdict(ASK, '`find` here also changes or runs things.');
  }
  // Any path that looks like a secret, in any command, means no: also where a symlink leads, and every file a
  // wildcard would match (~/.ss*/id_* matches no rule as written, but the shell turns it into ~/.ssh/id_rsa).
  for (const a of pathArgs) {
    if (!/[/~.]/.test(a) || a.startsWith('-') && !a.includes('=')) continue;
    const t = a.includes('=') ? a.split('=').slice(1).join('=') : a;
    if (/^[a-z]+:\/\//i.test(t)) continue;
    const isSecret = (x) => bothOf(norm(x, home)).some((n) => SECRET.some((re) => re.test(rel(n, home)) || re.test(n))) || SECRET.some((re) => re.test(x));
    if (isSecret(t)) return verdict(BLOCK, `${t} holds passwords or keys, so I never touch it.`);
    if (/[*?[]/.test(t)) {
      const hits = globHits(t, home, cwd);
      if (hits === null) return verdict(ASK, `I can't tell which files ${t} would match.`);
      if (hits.some(isSecret)) return verdict(BLOCK, `${t} matches files with passwords or keys, so I never touch it.`);
    }
  }
  // Making folders is like write_file making a new file: fine where writing is, a yes elsewhere, never where it's blocked.
  if (name === 'mkdir') {
    for (const a of pathArgs.filter((x) => !x.startsWith('-'))) {
      const w = checkWrite(a, home);
      if (w.level === BLOCK) return w.path.startsWith(slash(home) + '/') || /^\/(private\/)?tmp\//.test(w.path) ? verdict(BLOCK, w.reason) : verdict(ASK, w.reason);
    }
    return verdict(SAFE);
  }
  // sed -n '120,140p' file (or /word/p) only prints lines: read-only. Any other sed script (s///, w, e, -i) still asks.
  if (name === 'sed') {
    const flags = args.filter((a) => a.startsWith('-')), rest = args.filter((a) => !a.startsWith('-'));
    if (flags.length && flags.every((f) => f === '-n') && rest.length >= 1 && /^((\d+|\$)(,(\d+|\$))?|\/[^/;{}]*\/)p$/.test(rest[0])) return verdict(SAFE);
    return verdict(ASK);
  }
  if (READ_ONLY.has(name)) {
    const sub = READ_ONLY_SUB[name];
    if (sub && !sub.test(joined.trim())) return verdict(ASK);
    const side = sideEffect(name, args);
    if (side) return verdict(ASK, side);
    if (prog.assigns) return verdict(ASK, 'Setting environment variables can change what that program runs.');
    return verdict(SAFE);
  }
  return verdict(ASK);
}

// The files a wildcard argument would match (at most a few thousand), or null when that can't be worked out
// (** over whole trees, or no glob support): then it asks.
function globHits(t, home, cwd) {
  if (/\*\*/.test(t) || typeof fs.globSync !== 'function') return null;
  const pattern = t.startsWith('~/') ? path.join(slash(home), t.slice(2)) : t;
  try {
    const hits = fs.globSync(pattern, { cwd: slash(cwd ?? home) }).slice(0, 5000).map((h) => (isAbs(slash(h)) ? slash(h) : path.join(slash(cwd ?? home), slash(h))));
    return hits;
  } catch { return null; }
}

// Files a command writes with > >> &>, quotes removed; /dev/null and 2>&1 style copies left out.
const redirectTargets = (text) => [...text.matchAll(/(?:^|[^>])>{1,2}(?!&)\s*("[^"]*"|'[^']*'|[^\s;&|<>]+)/g)]
  .map((m) => m[1].replace(/^(['"])([\s\S]*)\1$/, '$2')).filter((t) => t && t !== '/dev/null');

/** run_command. SAFE runs, ASK needs the user's yes, BLOCK never runs. `exists(path)`, if given, lets a redirect
 *  that makes a new file run like write_file making one; without it every redirect asks. */
// "for i in 1 2 3; do echo $i > n$i.txt; done" (also with seq 1 N): a plain loop that only writes new files.
// It's run like the same commands written out one by one (so each file is checked like write_file making a new
// one); anything else in a loop still asks.
function unrollLoop(s) {
  const m = s.match(/^((?:(?:cd|mkdir(?:\s+-p)?)\s+[^;&|`$()]+?\s*&&\s*)*)for\s+([a-z_]\w*)\s+in\s+((?:[\w.-]+\s*)+|\$\(\s*seq\s+(\d+)\s+(\d+)\s*\))\s*;\s*do\s+([^;`$()]*?(?:\$\{?\2\}?[^;`$()]*?)*)\s*;?\s*done((?:\s*&&\s*[^;`$()|]+)?)$/);
  if (!m) return null;
  const [, pre, v, list, a, b, body, post] = m;
  const vals = a != null ? (Number(b) - Number(a) >= 0 && Number(b) - Number(a) < 100 ? Array.from({ length: Number(b) - Number(a) + 1 }, (_, i) => String(Number(a) + i)) : null) : list.trim().split(/\s+/);
  if (!vals || vals.length > 100 || !/^(echo|printf)\b/.test(body.trim()) || !/>/.test(body)) return null;
  return pre + vals.map((x) => body.replace(new RegExp(`\\$\\{?${v}\\}?(?!\\w)`, 'g'), x)).join(' && ') + post;
}

export function classifyCommand(cmd, home, { exists } = {}) {
  home = slash(home);
  const s0 = String(cmd ?? '').trim();
  const s = unrollLoop(s0) ?? s0;
  if (!s) return verdict(BLOCK, 'That command is empty.');
  if (/:\s*\(\s*\)\s*\{.*:\s*\|\s*:.*\}/.test(s)) return verdict(BLOCK, 'That is a fork bomb.');
  if (/\b(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b/.test(s) || /\bbase64\s+(-d|--decode|-D)\b[^|]*\|\s*(ba|z)?sh\b/.test(s)) return verdict(BLOCK, 'Downloading a script and running it straight away skips every check.');
  const parts = splitCommand(s);
  if (!parts) return verdict(ASK, "I couldn't read that command clearly.");
  let level = SAFE, reason = '';
  // The folder each part runs in, so relative paths after a cd are checked where they really point. null: unknown.
  let cwd = home;
  for (const p of parts) {
    let v = checkSimple(p.text, p.pipeIn, home, cwd ?? home);
    if (cwd == null && v.level === SAFE) v = verdict(ASK, "I can't tell which folder that runs in.");
    for (const r of redirectTargets(p.text)) {
      const t = /^~/.test(r) || isAbs(slash(r)) ? r : path.join(cwd ?? home, slash(r));
      const w = checkWrite(t, home, { exists: !exists || !!exists(norm(t, home)) });
      if (w.level === BLOCK) return verdict(BLOCK, w.reason);
      if ((w.level === ASK || cwd == null || /[$`*?{]/.test(r)) && v.level === SAFE) v = verdict(ASK, w.reason);
    }
    const ws = words(p.text);
    if (ws[0] === 'cd') {
      const t = ws[1];
      if (!t || t === '~') cwd = home;
      else if (cwd == null || /[$`*?]/.test(t) || t === '-') cwd = null;
      else cwd = norm(/^~/.test(t) || isAbs(slash(t)) ? t : path.join(cwd, slash(t)), home);
    }
    if (v.level === BLOCK) return v;
    if (v.level === ASK && level === SAFE) { level = ASK; reason = v.reason; }
  }
  // Command substitution and background jobs change things in ways the parts don't show.
  if (level === SAFE && (/\$\(|`/.test(s) || /(^|\s)&\s*$/.test(s))) level = ASK;
  return verdict(level, reason);
}

// ---------- opening things and fetching pages ----------

const RUNNABLE = /\.(app|command|sh|bash|zsh|tool|terminal|scpt|scptd|applescript|workflow|action|pkg|mpkg|dmg|jar|py|rb|pl|js|mjs|exe|bat|cmd|ps1|shortcut|mobileconfig|kext|plugin|prefPane|saver|desktop|appimage|deb|rpm|run|flatpakref|flatpak|snap)\/?$/i;
// Where installed apps live: opening one there just starts it. Linux lists them as .desktop files.
const APP_DIRS = ['/Applications/', '/System/Applications/', '/System/Library/CoreServices/', '/usr/share/applications/', '/usr/local/share/applications/', '/var/lib/flatpak/exports/share/applications/', '/var/lib/snapd/desktop/applications/'];

/** open: websites and mail, documents and folders, and apps by name. Not scripts, installers or odd links. */
export function checkOpen(target, home, { executable = false } = {}) {
  home = slash(home);
  const t = String(target ?? '').trim();
  if (!t) return verdict(BLOCK, 'Nothing to open.');
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(t)?.[1]?.toLowerCase();
  if (scheme && !/^[a-z]:\\/i.test(t)) {
    if (scheme === 'http' || scheme === 'https' || scheme === 'mailto') return verdict(SAFE);
    return verdict(BLOCK, `I only open web and mail links, not ${scheme}: links.`);
  }
  if (/^[~/.]/.test(t) || /^[a-z]:\\/i.test(t)) {
    const abs = norm(t, home);
    const r = checkRead(abs, home);
    if (r.level === BLOCK) return verdict(BLOCK, r.reason);
    const inApps = APP_DIRS.some((d) => abs.startsWith(d));
    if ((RUNNABLE.test(abs) || executable) && !inApps) return verdict(BLOCK, `${t} is a script, installer or app that would run, so I won't open it. Open it yourself if you trust it.`);
    if (!inApps && SYSTEM_ROOTS.some((d) => abs === d || abs.startsWith(d + '/'))) return verdict(BLOCK, `${t} is a system file, so I won't open it.`);
    return verdict(SAFE);
  }
  if (/[/\\]|\.\./.test(t)) return verdict(BLOCK, `"${t}" isn't an app name.`);
  return verdict(SAFE);
}

const PRIVATE_HOST = /^(localhost|.*\.local|.*\.internal|0\.0\.0\.0|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|\[?::1\]?|\[?f[cd][0-9a-f:]*\]?|\[?fe80:[0-9a-f:]*\]?)$/i;

/** An IP address that isn't on the public internet: this computer, the local network, link-local (cloud metadata),
 *  carrier-grade NAT, multicast and reserved ranges, also written as IPv4 inside IPv6 (::ffff:127.0.0.1). */
export function isPrivateAddress(ip) {
  let a = String(ip ?? '').toLowerCase().replace(/^\[|\]$/g, '').replace(/%.*$/, '');
  const mapped = a.match(/^(?:0*:)*:?ffff:(?:0+:)?([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
  if (mapped) { const hi = parseInt(mapped[1], 16), lo = parseInt(mapped[2], 16); a = `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`; }
  const v4 = a.match(/^(?:.*:)?(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [b0, b1] = [Number(v4[1]), Number(v4[2])];
    return b0 === 0 || b0 === 10 || b0 === 127 || (b0 === 100 && b1 >= 64 && b1 <= 127) || (b0 === 169 && b1 === 254) || (b0 === 172 && b1 >= 16 && b1 <= 31)
      || (b0 === 192 && (b1 === 168 || (b1 === 0 && Number(v4[3]) === 0))) || (b0 === 198 && (b1 === 18 || b1 === 19)) || b0 >= 224;
  }
  if (!a.includes(':')) return false;
  return a === '::' || a === '::1' || /^f[cd]/.test(a) || /^fe[89ab]/.test(a) || /^ff/.test(a) || /^64:ff9b:/.test(a) || /^0*:/.test(a) && /^[0:]*(:1)?$/.test(a);
}

/** fetch_url: public web pages only, so a web page can't steer Sidekick into the user's router or local apps.
 *  The address a name resolves to is checked too, just before each request (tools.mjs). */
export function checkUrl(url) {
  let u;
  try { u = new URL(String(url)); } catch { return verdict(BLOCK, "That isn't a valid link."); }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return verdict(BLOCK, 'I only fetch http and https pages.');
  if (u.username || u.password) return verdict(BLOCK, 'Links with passwords in them are blocked.');
  const host = u.hostname.replace(/^\[|\]$/g, '');
  if (PRIVATE_HOST.test(u.hostname) || ((/^[\d.]+$/.test(host) || host.includes(':')) && isPrivateAddress(host))) return verdict(BLOCK, `${u.hostname} is on your own computer or network, so I won't fetch it.`);
  return verdict(SAFE);
}

/** A link the user clicked in a reply: opened in their browser, so web and mail links only (no file:, apps or scripts). */
export function checkLink(url) {
  let u;
  try { u = new URL(String(url)); } catch { return verdict(BLOCK, "That isn't a valid link."); }
  if (u.protocol === 'mailto:') return verdict(SAFE);
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return verdict(BLOCK, 'Only web and email links open from a reply.');
  if (u.username || u.password) return verdict(BLOCK, 'Links with passwords in them are blocked.');
  return verdict(SAFE);
}

/** A yes from the user, typed or spoken. */
export const isYes = (text) => /^\s*(y|yes|yeah|yep|yup|ok|okay|sure|go|go ahead|do it|run it|allow|confirm)\b/i.test(String(text ?? ''));

// ---------- drawing on screen ----------

/** What a draw call may do beyond drawing. kind: 'open' (a page in the browser, like a link in a reply), 'photo' and
 *  'favicon' (fetched for a card, like fetch_url), 'type' (into a field, like act_in_app: only with actions allowed;
 *  the macOS helper still refuses the HANDS_OFF apps), 'copy' (to the clipboard), 'button' (a next-step button:
 *  ask sends its words as a new request, open and copy follow the rules above). */
export function checkDrawAction({ kind, url, text, action } = {}, { allowActions = false } = {}) {
  switch (kind) {
    case 'open': return checkLink(url);
    case 'photo': case 'favicon': return checkUrl(url);
    case 'type':
      if (!allowActions) return verdict(BLOCK, 'Typing into apps is switched off (Allow File Edits & Commands).');
      if (!String(text ?? '').length || String(text).length > 2000) return verdict(BLOCK, 'Nothing (or far too much) to type.');
      return verdict(SAFE);
    case 'copy': return String(text ?? '').length && String(text).length <= 10_000 ? verdict(SAFE) : verdict(BLOCK, 'Nothing (or far too much) to copy.');
    case 'button': {
      const a = action ?? {};
      if (a.do === 'ask') return String(a.value ?? '').trim() ? verdict(SAFE) : verdict(BLOCK, 'A button needs words to ask.');
      if (a.do === 'open') return checkLink(a.value);
      if (a.do === 'copy') return checkDrawAction({ kind: 'copy', text: a.value });
      return verdict(BLOCK, 'A button can only ask, open a link or copy.');
    }
    case 'handoff': return verdict(SAFE);
    default: return verdict(BLOCK, `${kind} isn't something a drawing can do.`);
  }
}
