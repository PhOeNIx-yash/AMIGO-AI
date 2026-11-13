// gg-agent also rejects a stream's result promise when a request fails. Nothing awaits it while we iterate,
// so Node treats it as unhandled and exits. The error still reaches the caller through the events.
export function agentEvents(stream) {
  const events = stream[Symbol.asyncIterator](); // claim the events first so then() doesn't drain them
  stream.then(undefined, () => {});
  return events;
}

/** Calls onStall once nothing has arrived for `ms`. A tool running (it may be waiting on the user) pauses it. */
export function stallWatch(ms, onStall) {
  const tools = new Set();
  let timer = null, done = false;
  const arm = () => {
    clearTimeout(timer); timer = null;
    if (!done && !tools.size) timer = setTimeout(() => { done = true; onStall(); }, ms);
  };
  arm();
  return {
    event(e) {
      if (e?.type === 'tool_call_start') tools.add(e.toolCallId);
      else if (e?.type === 'tool_call_end') tools.delete(e.toolCallId);
      arm();
    },
    stop() { done = true; clearTimeout(timer); },
  };
}
