// The user's own API keys (Fish Audio), kept the safest way each system offers:
// - macOS: the login keychain, through the signed roamies-hands helper (secret-get/-set/-delete). Encrypted,
//   locked with their Mac login, readable without a prompt only by that helper.
// - Windows: DPAPI (encrypted for this Windows user only), stored in ~/.gg/sidekick/secrets.json.
// - Linux: the desktop's keyring (GNOME Keyring, KWallet or KeePassXC, through the Secret Service), with secret-tool.
// The secret travels on stdin/stdout, never in a command line. There's no plain-text fallback: if the safe
// store fails, the key isn't saved. ROAMIES_DATA_DIR test runs without the helper use a file in that folder.
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';

const NAME = /^[a-z0-9-]{1,40}$/;

// Run a command with `input` on stdin; resolves { code, stdout, stderr }, never rejects.
export const runInput = (cmd, args, input = '', timeout = 15000) => new Promise((res) => {
  let p;
  try { p = spawn(cmd, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] }); } catch (e) { return res({ code: -1, stdout: '', stderr: String(e) }); }
  let stdout = '', stderr = '';
  const t = setTimeout(() => p.kill(), timeout);
  p.stdout.on('data', (d) => { stdout += d; });
  p.stderr.on('data', (d) => { stderr += d; });
  p.on('error', (e) => { clearTimeout(t); res({ code: -1, stdout, stderr: String(e) }); });
  p.on('close', (code) => { clearTimeout(t); res({ code, stdout, stderr }); });
  p.stdin.on('error', () => {});
  p.stdin.end(input);
});

/** Runs load once and resolves when that first try is done. If it fails (the keychain is locked, or its prompt went
 *  unanswered) it tries again in the background after each delay, the last one repeating, until it works. */
export function retryLoad(load, { delays = [30_000, 60_000, 120_000, 300_000, 600_000], schedule = setTimeout, onError = () => {} } = {}) {
  let n = 0;
  const attempt = () => Promise.resolve().then(load).catch((e) => {
    onError(e);
    schedule(attempt, delays[Math.min(n++, delays.length - 1)])?.unref?.();
  });
  return attempt();
}

const lastJson = (s) => { try { return JSON.parse(String(s).trim().split('\n').pop() || '{}'); } catch { return {}; } };

/** A secret store for this machine. `exec` is runInput (tests swap it). */
export function createSecrets({ platform = process.platform, hands = '', dataDir = '', exec = runInput } = {}) {
  const check = (name) => { if (!NAME.test(name)) throw new Error('bad secret name'); };

  if (platform === 'darwin' && hands) {
    return {
      kind: 'keychain',
      async get(name) {
        check(name);
        const r = lastJson((await exec(hands, ['secret-get', name])).stdout);
        if (!r.ok) throw new Error(r.error || 'keychain unavailable');
        return r.found ? r.value : null;
      },
      async set(name, value) {
        check(name);
        const r = lastJson((await exec(hands, ['secret-set', name], value)).stdout);
        if (!r.ok) throw new Error(r.error || 'keychain unavailable');
      },
      async delete(name) {
        check(name);
        const r = lastJson((await exec(hands, ['secret-delete', name])).stdout);
        if (!r.ok) throw new Error(r.error || 'keychain unavailable');
      },
    };
  }

  // Encrypted blobs (Windows) or, in test runs only, plain values: one JSON file, owner-only.
  const fileStore = (file, seal, unseal, kind) => {
    const read = async () => { try { return JSON.parse(await fs.readFile(file, 'utf8')); } catch { return {}; } };
    const write = async (all) => {
      await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
      const tmp = `${file}.${process.pid}.tmp`;
      await fs.writeFile(tmp, JSON.stringify(all, null, 2), { mode: 0o600 });
      await fs.rename(tmp, file);
    };
    return {
      kind,
      async get(name) { check(name); const v = (await read())[name]; return v == null ? null : unseal(v); },
      async set(name, value) { check(name); const all = await read(); all[name] = await seal(value); await write(all); },
      async delete(name) { check(name); const all = await read(); if (name in all) { delete all[name]; await write(all); } },
    };
  };

  if (platform === 'win32') {
    const ps = (script, input) => exec('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script], input);
    const PRE = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Security; $i=[Console]::In.ReadToEnd().Trim();";
    const dpapi = async (script, input) => {
      const r = await ps(`${PRE} ${script}`, input);
      const out = r.stdout.trim();
      if (r.code !== 0 || !out) throw new Error('Windows could not protect the key');
      return out;
    };
    return fileStore(
      path.join(dataDir, 'secrets.json'),
      (v) => dpapi("[Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($i),$null,'CurrentUser'))", v),
      (b) => dpapi("[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($i),$null,'CurrentUser'))", b),
      'dpapi',
    );
  }

  if (platform === 'linux') {
    const attrs = (name) => ['service', 'com.kenkai.roamies', 'account', name];
    // secret-tool missing, or no keyring answering on the session bus: say which, so it can be fixed.
    const problem = (r) => {
      if (r.code === -1 && /ENOENT/.test(r.stderr)) return 'secret-tool isn\'t installed (it comes in libsecret-tools on Debian and Ubuntu, libsecret on Fedora and Arch)';
      const why = String(r.stderr || '').trim().split('\n').pop().slice(0, 160);
      return `the keyring didn't answer${why ? ` (${why})` : ''}; a keyring app such as GNOME Keyring or KWallet needs to be running`;
    };
    return {
      kind: 'keyring',
      async get(name) {
        check(name);
        const r = await exec('secret-tool', ['lookup', ...attrs(name)]);
        if (r.code === 0) return r.stdout || null;
        // Not found: exit 1 with nothing said. Anything said is a keyring that couldn't be reached.
        if (r.code === 1 && !String(r.stderr).trim()) return null;
        throw new Error(problem(r));
      },
      async set(name, value) {
        check(name);
        const r = await exec('secret-tool', ['store', '--label', `Roamies: ${name}`, ...attrs(name)], value);
        if (r.code !== 0) throw new Error(problem(r));
      },
      async delete(name) {
        check(name);
        const r = await exec('secret-tool', ['clear', ...attrs(name)]);
        if (r.code !== 0 && String(r.stderr).trim()) throw new Error(problem(r));
      },
    };
  }

  if (process.env.ROAMIES_DATA_DIR) return fileStore(path.join(dataDir, 'test-secrets.json'), (v) => v, (v) => v, 'test-file');
  return { kind: 'none', get: async () => null, set: async () => { throw new Error('no safe place to keep keys on this system'); }, delete: async () => {} };
}
