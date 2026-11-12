// What the model is told about the computer it's on, and which of the hands helper's features this one has.
// Pure, so the wording for each system is tested without running on it (test/platform.test.mjs).

/** The prompt's platform line. "darwin" alone left the model using PC habits on a Mac (Cmd+End, cat -A), and the
 *  same goes the other way on Windows (Cmd, ls, grep). */
export function platformLine(platform, { macVersion = '', arch = '', release = '', session = '', desktop = '', distro = '' } = {}) {
  if (platform === 'darwin') {
    return `Platform: macOS${macVersion ? ` ${macVersion}` : ''} on ${arch === 'arm64' ? 'Apple silicon' : 'Intel'}. Use Mac keys: Cmd, not Ctrl; the end and start of a document are Cmd+Down and Cmd+Up, of a line Cmd+Right and Cmd+Left (Home and End don't move the cursor in most Mac apps). Shell commands are the macOS (BSD) versions, not GNU: no cat -A, grep -P, date -d or sed -i without ''.`;
  }
  if (platform === 'win32') {
    const build = +String(release).split('.')[2] || 0;
    const name = build ? `Windows ${build >= 22000 ? '11' : '10'}` : 'Windows';
    return `Platform: ${name} on ${arch === 'arm64' ? 'Arm' : 'x64'}. Use Windows keys: Ctrl, not Cmd (in act_in_app steps cmd means Ctrl); the end and start of a document are Ctrl+End and Ctrl+Home, of a line End and Home. run_command runs in cmd.exe: use Windows commands (dir, type, where, findstr) or powershell -NoProfile -Command "…", not Unix ones like ls -la, cat, grep or sed. Paths look like C:\\Users\\name, and ~ is the user's folder.`;
  }
  if (platform === 'linux') {
    const wayland = session !== 'x11';
    // XDG_CURRENT_DESKTOP reads "ubuntu:GNOME": the last part is the desktop itself.
    const de = String(desktop).split(':').filter(Boolean).pop() ?? '';
    return `Platform: Linux${distro ? ` (${distro})` : ''} on ${arch === 'arm64' ? 'Arm' : 'x64'}, ${de ? `the ${de} desktop` : 'a desktop'} on ${wayland ? 'Wayland' : 'X11'}. Use PC keys: Ctrl, not Cmd; the end and start of a document are Ctrl+End and Ctrl+Home, of a line End and Home. run_command runs in bash with the GNU tools (grep -P, sed -i with no '', date -d all work), not the macOS ones. Software comes from the distro's own package manager (apt, dnf, pacman, zypper) or Flatpak or Snap: check which is there before naming one, and installing needs the user's password, so give them the command instead of running sudo. Paths look like /home/name, and ~ is the user's folder.${wayland ? " Wayland keeps apps from seeing the pointer, clicks and keys in other apps: you can't tell where the user's cursor is or what they clicked, so a draw beat never waits for a click or hover (beats play one after another), and nothing follows their cursor." : ''}`;
  }
  return `Platform: ${platform}.`;
}

/** Linux: "x11" or "wayland". The app says which (ROAMIES_SESSION); run without it, the same signs the app reads. */
export function linuxSession(env = process.env) {
  if (env.ROAMIES_SESSION === 'x11' || env.ROAMIES_SESSION === 'wayland') return env.ROAMIES_SESSION;
  return String(env.XDG_SESSION_TYPE ?? '').toLowerCase() === 'wayland' || env.WAYLAND_DISPLAY ? 'wayland' : 'x11';
}

/** Whether the app can see the pointer and clicks over other apps: draw beats that wait for a click or hover,
 *  the Cursor size and following the cursor all need it. Everywhere but a Wayland session. */
export const seesPointer = (platform, session) => platform !== 'linux' || session === 'x11';

/** Whether the app can hold global shortcuts and watch the talk keys itself. On Wayland no app can: there the
 *  user binds keys to `roamies --summon`, `--talk` and the rest in the desktop's keyboard settings. */
export const hasHotkeys = (platform, session) => platform !== 'linux' || session === 'x11';

/** Voice in: Apple's speech on macOS, Parakeet on the PC on Windows and Linux. */
export const hasVoice = (platform) => platform === 'darwin' || platform === 'win32' || platform === 'linux';

/** The speech model has to be downloaded first (Parakeet): Windows and Linux. */
export const needsVoiceModel = (platform) => platform === 'win32' || platform === 'linux';

/** Whether the hands helper's tools are on: always on macOS (they explain what's missing), elsewhere when the
 *  helper is there (Roamies sets ROAMIES_HANDS to it). */
export const hasHands = (platform, hands) => platform === 'darwin' || !!hands;

/** Ask About an Area (the snap shortcut and menu item): the helper's area selection, on macOS and Windows.
 *  Linux has no hands helper yet, so none of the three below. */
export const canSnap = (platform, hands) => platform === 'darwin' || (platform === 'win32' && !!hands);

/** Circling something while holding the talk keys: the voice helper draws it, on macOS and Windows. */
export const canCircle = (platform) => platform === 'darwin' || platform === 'win32';

/** What to say when the voice helper can't use the microphone, from its no_input reason (the Mac helper sends
 *  none: it only reports a missing mic). */
export function micTrouble(platform, reason, detail = '') {
  const win = platform === 'win32';
  const sound = win ? 'Settings > System > Sound > Input' : 'your sound settings';
  switch (reason) {
    case 'busy':
      return `Another app has the microphone to itself. Close anything that might be recording (calls, streaming, recorders) and try again.${win ? ' Or untick "Allow applications to take exclusive control" in the mic\'s properties.' : ''}`;
    case 'blocked':
      return win
        ? 'Windows won\'t let me use the microphone. In the Privacy settings I just opened, turn on microphone access and "Let desktop apps access your microphone". If they\'re on already, antivirus or work settings may be blocking it.'
        : 'The system won\'t let me use the microphone. Check your privacy and sound settings, then try again.';
    case 'lost':
      return 'The microphone cut out: it was unplugged, the input changed, or a Bluetooth headset switched modes. Try again.';
    case 'failed':
      return `I couldn't open the microphone${detail ? ` (${String(detail).slice(0, 120)})` : ''}. Check it is the input picked in ${sound}, then try again.`;
    default:
      return `I can't find a microphone. Plug one in and try again.${platform === 'darwin' ? '' : ` If one is plugged in, check it is the input picked in ${sound}.`}`;
  }
}
