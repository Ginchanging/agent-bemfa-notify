---
name: agent-notify
description: Push a one-line status to the user's desk display or phone (Bemfa MQTT) with the notify_board MCP tool when a long task finishes, when you are blocked on the user, or when something failed.
---

# Reporting status with `notify_board`

The user may be away from the screen. A hook already sends a generic "done"
message when a long turn ends; calling `notify_board` replaces it with your own,
more useful summary for that turn.

Call `notify_board` (from the `agent-notify` MCP server):

- **kind `done`**: you finished a task that took a while. Say what is now true,
  e.g. "Firmware built and flashed; all 5 pages verified".
- **kind `attention`**: you are blocked and need the user, e.g. a decision,
  credentials, or a physical action ("Press BOOT on the board").
- **kind `error`**: the task failed and you can't continue, e.g. "Build failed:
  missing ESP-IDF; see terminal".

Rules:

- One short sentence, under about 60 characters, in the user's language.
- At most one call per outcome; never for routine progress or quick answers.
- Never put secrets, tokens or personal data in the message: it travels through
  a third-party cloud (bemfa.com).
