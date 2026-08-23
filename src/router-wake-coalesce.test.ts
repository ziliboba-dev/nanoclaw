/**
 * Wake-coalescing window (src/router.ts, scheduleWakeCoalesced): two
 * near-simultaneous engaged messages for the same session should land in
 * inbound.db together and produce a single wakeContainer call, instead of
 * each independently triggering the agent.
 */
import Database from 'better-sqlite3';
import fs from 'fs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  initTestDb,
  closeDb,
  runMigrations,
  createAgentGroup,
  createMessagingGroup,
  createMessagingGroupAgent,
} from './db/index.js';
import { inboundDbPath } from './mailbox/sqlite/paths.js';
import { findSession } from './db/sessions.js';
import type { InboundEvent } from './channels/adapter.js';

// This file's own module registry gets NANOCLAW_WAKE_COALESCE_MS=1000
// (overriding vitest.setup.ts's test-wide default of 0) so router.js's
// coalescing path is actually exercised here.
process.env.NANOCLAW_WAKE_COALESCE_MS = '1000';

vi.mock('./container-runner.js', () => ({
  wakeContainer: vi.fn().mockResolvedValue(true),
  isContainerRunning: vi.fn().mockReturnValue(false),
  getActiveContainerCount: vi.fn().mockReturnValue(0),
  killContainer: vi.fn(),
}));

vi.mock('./config.js', async () => {
  const actual = await vi.importActual('./config.js');
  return { ...actual, DATA_DIR: '/tmp/nanoclaw-test-wake-coalesce' };
});

function now() {
  return new Date().toISOString();
}

const TEST_DIR = '/tmp/nanoclaw-test-wake-coalesce';

beforeEach(async () => {
  if (fs.existsSync(TEST_DIR)) fs.rmSync(TEST_DIR, { recursive: true });
  fs.mkdirSync(TEST_DIR, { recursive: true });

  const db = await initTestDb();
  await runMigrations(db);

  await createAgentGroup({
    id: 'ag-1',
    name: 'Test Agent',
    folder: 'test-agent',
    agent_provider: null,
    created_at: now(),
  });
  await createMessagingGroup({
    id: 'mg-1',
    channel_type: 'discord',
    platform_id: 'chan-123',
    name: 'General',
    is_group: 1,
    unknown_sender_policy: 'public',
    created_at: now(),
  });
  await createMessagingGroupAgent({
    id: 'mga-1',
    messaging_group_id: 'mg-1',
    agent_group_id: 'ag-1',
    engage_mode: 'pattern',
    engage_pattern: '.',
    sender_scope: 'all',
    ignored_message_policy: 'drop',
    session_mode: 'shared',
    priority: 0,
    created_at: now(),
  });

  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(async () => {
  vi.useRealTimers();
  await closeDb();
  if (fs.existsSync(TEST_DIR)) fs.rmSync(TEST_DIR, { recursive: true });
});

function event(id: string, text: string): InboundEvent {
  return {
    channelType: 'discord',
    platformId: 'chan-123',
    threadId: null,
    message: { id, kind: 'chat', content: JSON.stringify({ sender: 'User', text }), timestamp: now() },
  };
}

describe('router wake coalescing', () => {
  it('batches two near-simultaneous messages into one wake', async () => {
    const { routeInbound } = await import('./router.js');
    const { wakeContainer } = await import('./container-runner.js');

    await routeInbound(event('msg-1', 'here is the forward'));
    await vi.advanceTimersByTimeAsync(200);
    await routeInbound(event('msg-2', 'and my note about it'));

    // Still inside the coalesce window — nothing should have woken yet.
    expect(wakeContainer).not.toHaveBeenCalled();

    const session = await findSession('mg-1', null);
    expect(session).toBeDefined();

    let rows = new Database(inboundDbPath('ag-1', session!.id))
      .prepare('SELECT id, trigger FROM messages_in ORDER BY seq')
      .all() as Array<{ id: string; trigger: number }>;
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.trigger === 0)).toBe(true);

    // Close the window (anchored on msg-1: fires 1000ms after msg-1, i.e.
    // 800ms after the point we're already at).
    await vi.advanceTimersByTimeAsync(800);

    expect(wakeContainer).toHaveBeenCalledTimes(1);

    rows = new Database(inboundDbPath('ag-1', session!.id))
      .prepare('SELECT id, trigger FROM messages_in ORDER BY seq')
      .all() as Array<{ id: string; trigger: number }>;
    expect(rows.find((r) => r.id.includes('msg-1'))!.trigger).toBe(0);
    expect(rows.find((r) => r.id.includes('msg-2'))!.trigger).toBe(1);
  });

  it('wakes once per burst, not once per message, for 3+ messages', async () => {
    const { routeInbound } = await import('./router.js');
    const { wakeContainer } = await import('./container-runner.js');

    await routeInbound(event('msg-a', 'one'));
    await vi.advanceTimersByTimeAsync(100);
    await routeInbound(event('msg-b', 'two'));
    await vi.advanceTimersByTimeAsync(100);
    await routeInbound(event('msg-c', 'three'));
    await vi.advanceTimersByTimeAsync(1000);

    expect(wakeContainer).toHaveBeenCalledTimes(1);
  });

  it('does not extend the window on later messages (fixed anchor)', async () => {
    const { routeInbound } = await import('./router.js');
    const { wakeContainer } = await import('./container-runner.js');

    await routeInbound(event('msg-x', 'first'));
    await vi.advanceTimersByTimeAsync(900);
    await routeInbound(event('msg-y', 'second, close to the deadline'));

    // Anchor fires 1000ms after msg-x regardless of msg-y's arrival —
    // that's 100ms from here, not another full 1000ms.
    await vi.advanceTimersByTimeAsync(100);
    expect(wakeContainer).toHaveBeenCalledTimes(1);
  });
});
