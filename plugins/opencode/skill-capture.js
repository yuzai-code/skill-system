// skill-capture.js — OpenCode plugin for skill-system.
//
// Subscribes to tool.execute.before (count + identify tool) and
// tool.execute.after (detect errors + recoveries), finalizes at
// session.idle, and injects an offer via tui.prompt.append when the
// gate fires.
//
// Field schema (verified against OpenCode 1.x via plugin_debug.log):
//   tool.execute.before/after input: { tool, sessionID, callID }
//   session.idle event.properties: { sessionID, ... }
//
// We count on `before` (always fires) and use `after` only for error
// detection (fires after permission granted + execution done).

import { appendFile, mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const HOME = homedir();
const STATE_DIR = join(HOME, ".skill-system", "state");
const SKILL_PROFILE_BIN = join(HOME, ".skill-system", "bin", "skill-profile");
const DBG = join(STATE_DIR, "plugin_debug.log");

async function dbg(line) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    await appendFile(DBG, new Date().toISOString() + " " + line + "\n");
  } catch (e) { /* best-effort */ }
}

// Detect error from OpenCode's real output schema:
//   { title, metadata, output, attachments }
// No explicit error flag — scan output text + metadata.
const _ERR_RE = /^(error|fatal|panic|traceback)|\b(failed|denied|not found|permission denied|exception|exited with)\b/i;
function _isErrorOutput(output, tool) {
  if (!output || typeof output !== "object") return false;
  if (output.isError === true || output.error === true) return true;
  if (output.ok === false) return true;
  // metadata may carry an error/status field
  const meta = output.metadata;
  if (meta && typeof meta === "object") {
    if (meta.error || meta.isError === true) return true;
    const exitCode = meta.exitCode ?? meta.exit_code ?? meta.exitStatus;
    if (typeof exitCode === "number" && exitCode !== 0) return true;
  }
  // output.output is the stdout/stderr string for bash-type tools
  const out = output.output;
  if (typeof out === "string" && out && _ERR_RE.test(out)) return true;
  return false;
}

function _toolsRelated(a, b) {
  if (!a || !b || a === "unknown" || b === "unknown") return false;
  if (a === b) return true;
  const pa = a.replace(/[_.]/g, "-").split("-")[0];
  const pb = b.replace(/[_.]/g, "-").split("-")[0];
  return pa === pb && pa !== "";
}

export const SkillCapture = async ({ client, $ }) => {
  const sessions = new Map();

  function getBuilder(sessionID) {
    if (!sessions.has(sessionID)) {
      sessions.set(sessionID, {
        agent_tool: "opencode",
        session_id: sessionID,
        started_at: new Date().toISOString(),
        tool_calls: 0,
        distinct: new Set(),
        errors: 0,
        recoveries: 0,
        corrections: 0,
        turns: 0,
        last_error_tool: null,
        // track callIDs we've seen on before, so after can correlate
        pending: new Map(), // callID -> tool name
      });
    }
    return sessions.get(sessionID);
  }

  function _toProfile(b) {
    return {
      schema_version: 1,
      session_id: b.session_id,
      agent_tool: b.agent_tool,
      started_at: b.started_at,
      ended_at: new Date().toISOString(),
      tool_calls: b.tool_calls,
      distinct_tools: Array.from(b.distinct),
      errors_encountered: b.errors,
      error_recoveries: b.recoveries,
      user_corrections: b.corrections,
      turns: b.turns,
      signals: {},
    };
  }

  async function _evaluateAndMaybeOffer(profile) {
    let result;
    try {
      const out = await $`${SKILL_PROFILE_BIN} --from-stdin --json`.quiet().json();
      // feed via stdin: we need to pipe the profile JSON in. Bun $ with
      // .json() parses stdout; we pass input via a temp file instead.
    } catch (e) {
      await dbg("offer pipe-fail " + String(e).slice(0, 120));
    }
    // Use --from-file to avoid stdin pipe complexity.
    const tmpProfile = join(STATE_DIR, `oc_profile_${profile.session_id}.json`);
    try {
      await mkdir(STATE_DIR, { recursive: true });
      await writeFile(tmpProfile, JSON.stringify(profile), "utf-8");
      const out = await $`${SKILL_PROFILE_BIN} --from-file ${tmpProfile} --agent-tool opencode --json`.quiet().json();
      if (!out?.should_offer || !out?.message) {
        return;
      }
      try {
        await client.tui.prompt.append({ text: out.message });
        await dbg("offer injected via tui.prompt.append");
      } catch (e) {
        await dbg("tui.prompt.append failed, writing fallback: " + String(e).slice(0, 120));
        await writeFile(join(STATE_DIR, "last_offer.txt"), out.message + "\n", "utf-8");
      }
    } catch (e) {
      await dbg("offer eval fail: " + String(e).slice(0, 200));
    }
  }

  return {
    "tool.execute.before": async (input, output) => {
      const sid = input?.sessionID || input?.sessionId || "unknown";
      const tool = input?.tool || "unknown";
      const callID = input?.callID;
      const b = getBuilder(sid);
      b.tool_calls += 1;
      b.distinct.add(tool);
      if (callID) b.pending.set(callID, tool);
    },

    "tool.execute.after": async (input, output) => {
      const sid = input?.sessionID || input?.sessionId || "unknown";
      const b = getBuilder(sid);
      const tool = input?.tool || "unknown";
      const callID = input?.callID;
      const errored = _isErrorOutput(output, tool);
      if (errored) {
        b.errors += 1;
        b.last_error_tool = tool;
      } else if (b.last_error_tool && _toolsRelated(b.last_error_tool, tool)) {
        b.recoveries += 1;
        b.last_error_tool = null;
      } else {
        b.last_error_tool = null;
      }
      if (callID) b.pending.delete(callID);
    },

    "session.idle": async ({ event }) => {
      const sid = event?.properties?.sessionID
        || event?.properties?.sessionId
        || event?.sessionID || "unknown";
      const b = getBuilder(sid);
      b.ended_at = new Date().toISOString();
      b.turns += 1;
      const profile = _toProfile(b);
      await dbg(`session.idle tool_calls=${profile.tool_calls} errors=${profile.errors_encountered} recoveries=${profile.error_recoveries} distinct=${profile.distinct_tools.length}`);
      await _evaluateAndMaybeOffer(profile);
    },
  };
};

export default SkillCapture;
