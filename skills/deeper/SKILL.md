---
name: deeper
description: Use when Louis invokes /deeper, or asks for a deeper version of a task or prompt, "go deeper", "what would a pro do here", "make this less shallow".
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, WebSearch, WebFetch
---

# Deeper

The task is the one Louis names after `/deeper`; with nothing after it, the task he
last asked for in this session. His instruction, verbatim:

> I want a deeper version of this task, not a bigger one. Before rewriting it: (1) say
> what a top professional in this field would do here that I'm not asking for; (2) name
> the rawest material they would start from instead of what has already been digested
> for me; (3) name the specific methods and tools they would use; (4) say what, other
> than you or me, would show the result is right. Then rewrite my prompt to ask for
> exactly that.

Reply in five numbered parts, (1) to (4) then the rewritten prompt, and stop: he decides
whether to run it.

- Name the field, and answer as its practitioners would: real methods and tools by
  name, real sources by name and where they are, not categories.
- (2) is the primary material itself (raw data, logs, the source code, the original
  paper or dataset), never a summary, a dashboard, or a report about it.
- (4) is an external check that can fail: a test, a measurement, a held-out set, a
  primary record, an independent expert or tool. Never "review it carefully".
- Deeper, not bigger: the rewrite keeps the task's scope and goes down, not wide.
- The rewritten prompt is self-contained, in a block he can copy and paste.
