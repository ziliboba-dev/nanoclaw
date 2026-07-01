/**
 * Dependency guard for the OpenCode CLI integration point (host tree, vitest).
 *
 * add-opencode installs the `opencode-ai` CLI globally in the agent container
 * image via container/cli-tools.json. A globally-installed CLI binary is not
 * importable or typed, so neither `tsc` nor a runtime import can catch its
 * removal — only the container image build would. This structural test stands
 * in for that build leg: it parses cli-tools.json and asserts opencode-ai is
 * present with a pinned (non-latest) version. Drop or drift either and this
 * goes red.
 *
 * Pinning matters: the `opencode-ai` CLI version must match the
 * `@opencode-ai/sdk` version the container provider imports. An unpinned
 * `latest` would silently upgrade the CLI past the SDK's compatible range.
 */
import fs from 'fs';
import path from 'path';

import { describe, it, expect } from 'vitest';

function cliTools(): Array<{ name: string; version: string; onlyBuilt?: boolean }> {
  let dir = __dirname;
  for (let i = 0; i < 8; i++) {
    const candidate = path.join(dir, 'container', 'cli-tools.json');
    if (fs.existsSync(candidate)) return JSON.parse(fs.readFileSync(candidate, 'utf8'));
    dir = path.dirname(dir);
  }
  throw new Error('container/cli-tools.json not found walking up from ' + __dirname);
}

describe('container/cli-tools.json installs the OpenCode CLI', () => {
  const tools = cliTools();
  const entry = tools.find((t) => t.name === 'opencode-ai');

  it('has an opencode-ai entry', () => {
    expect(entry).toBeDefined();
  });

  it('pins opencode-ai to a specific version (not latest)', () => {
    expect(entry?.version).toBeTruthy();
    expect(entry?.version).not.toBe('latest');
    expect(entry?.version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
