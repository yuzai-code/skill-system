// test_plugin_e2e.js — end-to-end test of skill-capture.js plugin logic.
//
// Imports the ACTUAL production plugin from ~/.config/opencode/plugins/
// and replays a realistic OpenCode event sequence with the verified
// payload schema:
//   tool.execute.before input: { tool, sessionID, callID }
//   tool.execute.after  input: { tool, sessionID, callID, args }
//                        output: { title, metadata, output, attachments }
//   session.idle        event.properties: { sessionID }
//
// Verifies:
//   1. before increments tool_calls + distinct
//   2. after with error output increments errors
//   3. after success of related tool after error increments recoveries
//   4. session.idle calls skill-profile -> emits offer -> records cooldown
//   5. second idle (cooldown) does not re-offer

import { rm, mkdir, writeFile, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { execSync } from "node:child_process";

const HOME = homedir();
const STATE_DIR = join(HOME, ".skill-system", "state");
const PLUGIN = join(HOME, ".config", "opencode", "plugins", "skill-capture.js");

let PASS = 0, FAIL = 0;
function ok(m) { PASS++; console.log(`  \x1b[32m✓\x1b[0m ${m}`); }
function fail(m) { FAIL++; console.log(`  \x1b[31m✗\x1b[0m ${m}`); }
function check(cond, m) { cond ? ok(m) : fail(m); }

async function main() {
  // Isolate state: reset offer gate + clear debug log
  try { await rm(join(STATE_DIR, "offer_state.json"), { force: true }); } catch {}
  try { await rm(join(STATE_DIR, "plugin_debug.log"), { force: true }); } catch {}
  try { await rm(join(STATE_DIR, "codex_tool.log"), { force: true }); } catch {}

  // Build a fake client + $ stub. The plugin uses:
  //   client.tui.prompt.append({text})
  //   $`...`.quiet().json()
  let injectedOffer = null;
  const fakeClient = {
    tui: { prompt: { append: async ({ text }) => { injectedOffer = text; } } },
    app: { log: async () => {} },
  };
  // $ stub: the plugin calls $`SKILL_PROFILE_BIN --from-file X --agent-tool opencode --json`.quiet().json()
  // We intercept by actually running skill-profile via execSync.
  const fake$ = (strings, ...vals) => {
    // Reconstruct the command string
    let cmd = strings[0];
    for (let i = 0; i < vals.length; i++) cmd += String(vals[i]) + strings[i + 1];
    return {
      quiet() { return this; },
      async json() {
        // Parse the cmd to find --from-file and --agent-tool
        const fileMatch = cmd.match(/--from-file\s+(\S+)/);
        const toolMatch = cmd.match(/--agent-tool\s+(\S+)/);
        if (!fileMatch) return { should_offer: false, message: "" };
        const profileJson = await readFile(fileMatch[1], "utf-8");
        // Run skill-profile for real
        const out = execSync(
          `${join(HOME, ".skill-system", "bin", "skill-profile")} --from-stdin --agent-tool ${toolMatch ? toolMatch[1] : "opencode"} --json`,
          { input: profileJson, encoding: "utf-8", timeout: 10000 }
        );
        return JSON.parse(out);
      },
    };
  };
  fake$.raw = (strings, ...vals) => strings.reduce((s, str, i) => s + (i > 0 ? String(vals[i - 1]) : "") + str, "");

  // Import the production plugin
  const mod = await import(PLUGIN);
  const SkillCapture = mod.SkillCapture || mod.default;
  check(typeof SkillCapture === "function", "plugin exports SkillCapture function");

  const hooks = await SkillCapture({ client: fakeClient, $: fake$, project: { name: "test" }, directory: "/tmp" });
  check(typeof hooks["tool.execute.before"] === "function", "exposes tool.execute.before hook");
  check(typeof hooks["tool.execute.after"] === "function", "exposes tool.execute.after hook");
  check(typeof hooks["session.idle"] === "function", "exposes session.idle hook");

  const SID = "ses_test_e2e_001";
  // Replay a realistic complex session:
  //   read, read, edit(error), edit(recovery), bash, write
  // = 6 tool calls, 1 error, 1 recovery, 4 distinct tools
  console.log("\n=== replay event sequence ===");
  await hooks["tool.execute.before"]({ tool: "read", sessionID: SID, callID: "c1" }, {});
  await hooks["tool.execute.before"]({ tool: "read", sessionID: SID, callID: "c2" }, {});
  await hooks["tool.execute.before"]({ tool: "edit", sessionID: SID, callID: "c3" }, {});
  await hooks["tool.execute.after"]({ tool: "edit", sessionID: SID, callID: "c3", args: {} },
    { title: "edit", metadata: {}, output: "error: syntax invalid", attachments: undefined });
  await hooks["tool.execute.before"]({ tool: "edit", sessionID: SID, callID: "c4" }, {});
  await hooks["tool.execute.after"]({ tool: "edit", sessionID: SID, callID: "c4", args: {} },
    { title: "edit", metadata: {}, output: "ok", attachments: undefined });
  await hooks["tool.execute.before"]({ tool: "bash", sessionID: SID, callID: "c5" }, {});
  await hooks["tool.execute.after"]({ tool: "bash", sessionID: SID, callID: "c5", args: {} },
    { title: "bash", metadata: {}, output: "done", attachments: undefined });
  await hooks["tool.execute.before"]({ tool: "write", sessionID: SID, callID: "c6" }, {});
  await hooks["tool.execute.after"]({ tool: "write", sessionID: SID, callID: "c6", args: {} },
    { title: "write", metadata: {}, output: "", attachments: undefined });
  ok("replayed 6 tool calls (1 error, 1 recovery)");

  // Trigger session.idle -> should emit offer
  console.log("\n=== session.idle (1st) — should emit offer ===");
  await hooks["session.idle"]({ event: { type: "session.idle", properties: { sessionID: SID } } });

  check(injectedOffer !== null, "offer injected via tui.prompt.append");
  check(injectedOffer && injectedOffer.includes("complexity threshold"), "offer text mentions threshold");
  check(injectedOffer && injectedOffer.includes("Ask the user"), "offer mandates ask-first");

  // Verify cooldown state advanced to WAITING
  const state1 = JSON.parse(await readFile(join(STATE_DIR, "offer_state.json"), "utf-8"));
  check(state1.opencode && state1.opencode.state === "waiting", `state=WAITING after emit (got ${state1.opencode?.state})`);

  // Verify the profile recorded has correct counts
  const prof = state1.opencode.last_profile;
  check(prof.tool_calls === 6, `profile tool_calls=6 (got ${prof.tool_calls})`);
  check(prof.errors_encountered === 1, `profile errors=1 (got ${prof.errors_encountered})`);
  check(prof.error_recoveries === 1, `profile recoveries=1 (got ${prof.error_recoveries})`);
  check(prof.distinct_tools.length === 4, `profile distinct=4 (got ${prof.distinct_tools.length})`);

  // 2nd idle — should NOT re-offer (WAITING cooldown)
  console.log("\n=== session.idle (2nd) — should be blocked by WAITING ===");
  injectedOffer = null;
  await hooks["session.idle"]({ event: { type: "session.idle", properties: { sessionID: SID } } });
  check(injectedOffer === null, "no re-offer during WAITING");

  // Simulate skill_manage(create) -> record_create -> COOLDOWN
  console.log("\n=== record_create -> COOLDOWN ===");
  execSync(`${join(HOME, ".skill-system", "bin", "skill-profile")} --record-create --agent-tool opencode`, { encoding: "utf-8" });
  const state2 = JSON.parse(await readFile(join(STATE_DIR, "offer_state.json"), "utf-8"));
  check(state2.opencode.state === "cooldown", `state=COOLDOWN after create (got ${state2.opencode.state})`);

  // 3rd idle during COOLDOWN — blocked
  console.log("\n=== session.idle (3rd) — blocked by COOLDOWN ===");
  injectedOffer = null;
  await hooks["session.idle"]({ event: { type: "session.idle", properties: { sessionID: SID } } });
  check(injectedOffer === null, "no offer during COOLDOWN");

  console.log(`\n${PASS} passed, ${FAIL} failed`);
  process.exit(FAIL > 0 ? 1 : 0);
}

main().catch(e => { console.error("FATAL:", e); process.exit(2); });
