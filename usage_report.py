#!/usr/bin/env python3
"""
NanoClaw v2 — отчёт по расходу токенов Claude Agent SDK.

Реальная структура на диске (выяснено по факту, не по документации):

  data/v2-sessions/{agent_group}/.claude-shared/projects/-workspace-agent/
      {session_uuid}.jsonl                       <- основной транскрипт топ-level сессии
      {session_uuid}.jsonl.rotated-{timestamp}    <- ротированный (старый) транскрипт той же сессии
      {session_uuid}/subagents/agent-{id}.jsonl   <- транскрипты сабагентов, запущенных этой сессией
      memory/MEMORY.md, feedback_*.md             <- файлы памяти агента (не usage, но размер влияет на system prompt)

Важно: .rotated-* файлы — это ПРОДОЛЖЕНИЕ той же сессии до ротации, не дубликат.
Чтобы правильно считать "рост истории внутри сессии", нужно сначала отсортировать
все файлы одной session_uuid по времени (rotated раньше, текущий .jsonl позже) и
склеить как один поток turns.

Usage в каждой строке лежит в turn['message']['usage'] (формат Claude Code SDK / Agent SDK)
либо в turn['usage'] напрямую — проверяем оба варианта на всякий случай.

Использование:
  python3 usage_report.py data/v2-sessions
  (запускать из корня nanoclaw-v2, либо передать абсолютный путь)
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

PROJECTS_SUBDIR = Path(".claude-shared/projects/-workspace-agent")

def safe_json_lines(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

def extract_usage(turn: dict):
    usage = turn.get("usage")
    if usage is None and isinstance(turn.get("message"), dict):
        usage = turn["message"].get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
    }

ROTATED_RE = re.compile(r"^(.*)\.jsonl\.rotated-(\d+)$")

def group_session_files(projects_dir: Path):
    """
    Возвращает dict: session_uuid -> [список файлов в хронологическом порядке]
    Учитывает .rotated-{timestamp} как более старые части той же сессии.
    """
    sessions = defaultdict(list)  # uuid -> list of (sort_key, path)
    for f in projects_dir.glob("*.jsonl*"):
        name = f.name
        m = ROTATED_RE.match(name)
        if m:
            uuid, ts = m.group(1), int(m.group(2))
            sessions[uuid].append((ts, f))
        elif name.endswith(".jsonl"):
            uuid = name[:-len(".jsonl")]
            # текущий файл всегда "самый новый" -> сортируем после rotated по дате мтайм
            sessions[uuid].append((f.stat().st_mtime if f.exists() else float("inf"), f))
    # отсортировать файлы внутри каждой сессии по ts/mtime
    for uuid in sessions:
        sessions[uuid].sort(key=lambda pair: pair[0])
    return sessions

def find_subagent_files(projects_dir: Path, session_uuid: str):
    subdir = projects_dir / session_uuid / "subagents"
    if not subdir.exists():
        return []
    return sorted(subdir.glob("agent-*.jsonl"))

def accumulate(bucket, files):
    """Прогоняет список файлов как один непрерывный поток turns, считает usage."""
    prev_input = None
    for _, f in files:
        for turn in safe_json_lines(f):
            usage = extract_usage(turn)
            if usage is None:
                continue
            bucket["turns"] += 1
            bucket["output_tokens"] += usage["output_tokens"]
            bucket["cache_creation"] += usage["cache_creation_input_tokens"]
            bucket["cache_read"] += usage["cache_read_input_tokens"]
            cur_input = usage["input_tokens"]
            if prev_input is None:
                bucket["first_turn_input"] += cur_input
            else:
                growth = cur_input - prev_input
                if growth > 0:
                    bucket["history_growth_input"] += growth
            prev_input = cur_input

def new_bucket():
    return {
        "turns": 0,
        "first_turn_input": 0,
        "history_growth_input": 0,
        "output_tokens": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "sessions": 0,
    }

def pct(part, total):
    if total <= 0:
        return "0.0"
    return f"{100 * part / total:.1f}"

def main():
    if len(sys.argv) > 1:
        sessions_root = Path(sys.argv[1])
    else:
        sessions_root = Path("data/v2-sessions")

    if not sessions_root.exists():
        print(f"Не найдена директория: {sessions_root}")
        print("Запусти из корня nanoclaw-v2, либо передай путь явно:")
        print("  python3 usage_report.py /home/nanoclaw/ideaProjects/nanoclaw-v2/data/v2-sessions")
        sys.exit(1)

    per_agent_group = defaultdict(new_bucket)      # основные диалоги
    subagent_bucket = new_bucket()                  # все сабагенты суммарно (отдельная категория расхода)
    subagent_bucket["sessions"] = 0

    agent_group_dirs = [d for d in sessions_root.iterdir() if d.is_dir()]
    if not agent_group_dirs:
        print(f"В {sessions_root} не найдено директорий agent_group.")
        sys.exit(1)

    for ag_dir in agent_group_dirs:
        projects_dir = ag_dir / PROJECTS_SUBDIR
        if not projects_dir.exists():
            continue
        bucket = per_agent_group[ag_dir.name]
        sessions = group_session_files(projects_dir)
        for session_uuid, files in sessions.items():
            bucket["sessions"] += 1
            accumulate(bucket, files)

            sub_files = find_subagent_files(projects_dir, session_uuid)
            if sub_files:
                subagent_bucket["sessions"] += 1
                accumulate(subagent_bucket, [(0, f) for f in sub_files])

    if not per_agent_group:
        print("Не найдено .claude-shared/projects/-workspace-agent ни в одной agent_group директории.")
        print("Возможно структура отличается дальше — пришли вывод:")
        print(f"  find {sessions_root} -maxdepth 5 -name '*.jsonl*'")
        sys.exit(1)

    print(f"{'Agent group':<32} {'Sessions':>9} {'Turns':>7} {'1й-turn(sys+tools)':>20} {'Рост истории':>14} {'Output':>9} {'CacheRead':>10}")
    print("-" * 110)

    totals = new_bucket()
    for name, b in sorted(per_agent_group.items(), key=lambda kv: -(kv[1]["first_turn_input"] + kv[1]["history_growth_input"])):
        print(f"{name:<32} {b['sessions']:>9} {b['turns']:>7} {b['first_turn_input']:>20} {b['history_growth_input']:>14} {b['output_tokens']:>9} {b['cache_read']:>10}")
        for k in totals:
            totals[k] += b[k]

    print("-" * 110)
    print(f"{'[субагенты, отдельно]':<32} {subagent_bucket['sessions']:>9} {subagent_bucket['turns']:>7} {subagent_bucket['first_turn_input']:>20} {subagent_bucket['history_growth_input']:>14} {subagent_bucket['output_tokens']:>9} {subagent_bucket['cache_read']:>10}")

    grand_input = totals["first_turn_input"] + totals["history_growth_input"]
    sub_input = subagent_bucket["first_turn_input"] + subagent_bucket["history_growth_input"]

    print()
    print("ИТОГО (основные диалоги, без субагентов):")
    print(f"  Sessions:                          {totals['sessions']}")
    print(f"  Turns:                              {totals['turns']}")
    print(f"  Input — первый turn (sys+tools):    {totals['first_turn_input']:>12}  ({pct(totals['first_turn_input'], grand_input)}% от input)")
    print(f"  Input — рост от истории диалога:    {totals['history_growth_input']:>12}  ({pct(totals['history_growth_input'], grand_input)}% от input)")
    print(f"  Output:                              {totals['output_tokens']:>12}")
    print(f"  Cache read:                          {totals['cache_read']:>12}")
    cache_eff = pct(totals["cache_read"], totals["cache_read"] + grand_input)
    print(f"  Эффективность кеша:                  {cache_eff}%")
    print()
    print("СУБАГЕНТЫ (параллельные child-агенты для research/browsing/extraction):")
    print(f"  Запущено сессий с сабагентами:        {subagent_bucket['sessions']}")
    print(f"  Input сабагентов суммарно:            {sub_input:>12}")
    print(f"  Output сабагентов суммарно:           {subagent_bucket['output_tokens']:>12}")
    if grand_input > 0:
        print(f"  Доля сабагентов от общего input:      {pct(sub_input, grand_input + sub_input)}%")
    print()
    print("Как читать:")
    print("  - Если 'первый turn (sys+tools)' большой и сессий много (низкий turns/session) —")
    print("    сессии создаются заново слишком часто вместо resume. Каждая новая сессия = full-price")
    print("    system prompt + CLAUDE.md + tool defs. Проверь логику переиспользования session_id в роутере.")
    print("  - Если 'рост истории' большой — копится контекст внутри долгих сессий (история чата,")
    print("    tool-result'ы). Решение — суммаризация старых turns / ограничение окна сообщений.")
    print("  - Если доля субагентов заметная — каждый research/browsing подзапрос стоит отдельного")
    print("    system prompt сабагента. Проверь, не дробится ли простая задача на сабагентов без нужды.")
    print("  - cache_read маленький относительно (cache_read + input) -> кеш почти не работает:")
    print("    либо паузы между сообщениями группы больше ~5 минут (TTL кеша истекает),")
    print("    либо начало system prompt не статично (любая правка в начале — и весь кеш ниже инвалидируется).")

if __name__ == "__main__":
    main()
