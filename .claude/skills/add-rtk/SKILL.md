---
name: add-rtk
description: Install rtk token-compression proxy into agent containers. Routes Bash tool calls through rtk for 60–90% token savings on dev commands (git, cargo, pytest, docker, kubectl, etc.).
---

# Add rtk

Install [rtk](https://github.com/rtk-ai/rtk) — a CLI proxy delivering 60–90% token savings on common dev commands (git, cargo, pytest, docker, kubectl, etc.) — and wire it transparently into agent containers via the Claude Code `PreToolUse` hook.

## What this sets up

- `rtk` binary installed on the host, then copied to `~/mount-bin/rtk` — a dedicated mount-source directory (see Step 1 note on why not `~/.local/bin`)
- `~/mount-bin/rtk` mounted read-only into the target agent group's containers at `/workspace/extra/rtk`
- `PreToolUse` hook in the agent group's `settings.json` so every Bash call is automatically filtered through rtk — no CLAUDE.md instructions needed

## Integration tests

This skill has **no in-tree integration test** by design. Its only functional reach-ins are runtime operator actions — the host-only `ncl groups config add-mount` (Step 3) and the `settings.json` `PreToolUse` hook write (Step 4) — neither of which leaves a line in the source tree whose deletion a test could catch. There are no package dependencies or Dockerfile edits to guard either. Conformance is idempotent apply + `REMOVE.md`; the mount and hook are verified at runtime (see Verify).

## Step 1 — Install rtk on the host

```bash
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh
```

The installer typically puts the binary at `~/.local/bin/rtk`. Find it:

```bash
find ~/.local ~/.cargo/bin ~/bin -name rtk 2>/dev/null
```

**Do not mount straight from `~/.local/bin`.** `src/modules/mount-security/index.ts` hardcodes `.local/bin` as a blocked mount pattern — it's the directory setup installs `onecli`/`claude` into and then executes by name as the operator, so a mount of it is a container-to-host code-execution primitive. The block cannot be overridden via `mount-allowlist.json`. Copy the binary to a dedicated mount-source directory instead:

```bash
mkdir -p ~/mount-bin
cp "$(find ~/.local ~/.cargo/bin ~/bin -name rtk 2>/dev/null | head -1)" ~/mount-bin/rtk
chmod +x ~/mount-bin/rtk
```

Verify:

```bash
~/mount-bin/rtk --version
```

## Step 2 — Identify the target agent group

```bash
ncl groups list
```

Note the group ID (e.g. `ag-1776342942165-ptgddd`). Repeat Steps 3–5 for each group.

## Step 3 — Mount rtk into the container config

Mount the host rtk binary read-only into the container with the host-only `add-mount` verb. It is idempotent — re-running skips the entry if it is already present. **The container path must be relative** — `mount-security` rejects absolute container paths and resolves relative ones under `/workspace/extra/`:

```bash
ncl groups config add-mount --id <group-id> \
  --host ~/mount-bin/rtk \
  --container rtk \
  --ro
```

This lands the binary at `/workspace/extra/rtk` inside the container. This verb is operator-only and runs host-side (via `/setup`, `/customize`, or `/manage-mounts`); it is rejected from inside a container.

The host root (`~/mount-bin`) must also be in the external mount allowlist at `~/.config/nanoclaw/mount-allowlist.json` for the mount to take effect at spawn. Entries there must be objects, not bare strings — a bare string silently normalizes to an empty path and matches nothing:

```json
{
  "allowedRoots": [
    { "path": "/home/<user>/mount-bin", "allowReadWrite": false, "description": "rtk and other agent-container tool mounts" }
  ],
  "blockedPatterns": []
}
```

Add the entry if it isn't already there (merge with any existing `allowedRoots`, don't overwrite).

Verify:

```bash
ncl groups config get --id <group-id>
# Look for the additionalMounts entry: hostPath .../mount-bin/rtk, containerPath "rtk"
```

## Step 4 — Add the PreToolUse hook to settings.json

Each agent group has a `settings.json` at:

```
data/v2-sessions/<group-id>/.claude-shared/settings.json
```

This file is mounted at `/home/node/.claude/settings.json` inside the container and is read by Claude Code for hooks, env, and model config.

Add the `PreToolUse` entry with `jq`. This drops any existing rtk Bash hook first, then appends a fresh one, so it is safe to re-run without creating duplicates:

```bash
SETTINGS="data/v2-sessions/<group-id>/.claude-shared/settings.json"

jq '.hooks.PreToolUse = ((.hooks.PreToolUse // [])
      | map(select((.hooks // []) | any(.command | test("rtk")) | not)))
    + [{"matcher":"Bash","hooks":[{"type":"command","command":"/workspace/extra/rtk hook claude"}]}]' \
  "$SETTINGS" > /tmp/rtk-settings.json && mv /tmp/rtk-settings.json "$SETTINGS"
```

The hook command uses the full mounted path (`/workspace/extra/rtk`), not a bare `rtk` — `/workspace/extra` is not on the container's `$PATH`.

## Step 5 — Restart the container

```bash
ncl groups restart --id <group-id>
```

## Verify

Confirm the binary is executable inside the container so a missing or non-executable mount surfaces immediately rather than as a silent hook failure:

```bash
docker exec "$(docker ps --filter "name=<group-id>" --format '{{.Names}}' | head -1)" /workspace/extra/rtk --version
```

Then ask the agent to run `git status` or any other supported command. rtk intercepts it silently. Check savings with:

```bash
~/mount-bin/rtk gain
```

## Troubleshooting

### `rtk: command not found` inside the container

Mount wasn't applied or container wasn't restarted. Check the host log for a rejection first:

```bash
grep "Additional mount REJECTED" logs/nanoclaw.error.log | tail -5
```

Common rejection reasons: an absolute `containerPath` (must be relative — Step 3), or a `hostPath` matching a blocked pattern like `.local/bin` (move the binary to `~/mount-bin` — Step 1). If nothing was rejected, confirm the mount is present and restart:

```bash
ncl groups config get --id <group-id>
# Look for the additionalMounts entry: hostPath .../mount-bin/rtk, containerPath "rtk"
ncl groups restart --id <group-id>
```

A restart without `--message` only kills the container — it respawns (and the mount takes effect) on the group's next incoming message, not immediately.

### Hook not firing

Verify the hook is in `settings.json`:

```bash
jq '.hooks.PreToolUse' data/v2-sessions/<group-id>/.claude-shared/settings.json
```

If missing, re-run Step 4.

### Binary won't execute — permission denied

```bash
chmod +x ~/mount-bin/rtk
```
