// Router's wake-coalescing window (src/router.ts) defaults to 1000ms in
// production. Tests assert wakeContainer synchronously after routeInbound
// resolves, which relies on the pre-coalescing behavior — disable the
// window by default so the existing suite doesn't need a 1s real-timer wait
// per test. Individual coalescing-behavior tests can override this before
// importing router.js.
process.env.NANOCLAW_WAKE_COALESCE_MS ??= '0';
