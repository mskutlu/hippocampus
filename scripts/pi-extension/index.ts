/**
 * Hippocampus extension for Pi (@earendil-works/pi-coding-agent).
 *
 * Pi deliberately ships without native MCP. This extension closes that gap:
 *
 *   1. Re-exposes the Hippocampus MCP tools through `pi.registerTool()`
 *      with their canonical JSON Schemas (mirrored as TypeBox so the LLM
 *      sees the same surface as Devin / Claude Code / etc).
 *   2. Spawns the `hippocampus-mcp` stdio server as a singleton child and
 *      forwards every tool call via JSON-RPC. Lazy startup; survives across
 *      tool invocations.
 *   3. Wires Pi's lifecycle events so memory survives compaction:
 *        - session_start       → open hippo session, prime additionalContext
 *        - before_agent_start  → log the ask, append fresh `hippo context`
 *                                snippet to the system prompt
 *        - session_shutdown    → tear down the MCP child cleanly
 *
 * Placeholders rendered at install time:
 *   __HIPPO_BIN__         absolute path to the `hippo` CLI
 *   __HIPPOCAMPUS_MCP__   command line that boots the MCP server
 *   __HIPPOCAMPUS_CLIENT__ stable client id (always "pi")
 *
 * Installed by Hippocampus into ~/.pi/agent/extensions/hippocampus/ via
 * `hippo register` (see src/hippocampus/clients/mcp_config.py:_install_pi_extension).
 */

import { spawn, type ChildProcessWithoutNullStreams, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type, type Static, type TSchema } from "typebox";

// ---------------------------------------------------------------------------
// Configuration (rendered at install time)
// ---------------------------------------------------------------------------

const HIPPO_BIN = "__HIPPO_BIN__";
const HIPPOCAMPUS_MCP = "__HIPPOCAMPUS_MCP__";
const HIPPOCAMPUS_CLIENT = "__HIPPOCAMPUS_CLIENT__";

// Soft cap on how much markdown we paste into the system prompt per turn.
const CONTEXT_BUDGET_CHARS = 3500;
const ASK_TRUNCATE_CHARS = 500;
const TOOL_CALL_TIMEOUT_MS = 30_000;

// ---------------------------------------------------------------------------
// MCP client — minimal JSON-RPC 2.0 over stdio
// ---------------------------------------------------------------------------

type JsonRpcRequest = {
	jsonrpc: "2.0";
	id: number;
	method: string;
	params?: unknown;
};

type JsonRpcResponse = {
	jsonrpc: "2.0";
	id: number;
	result?: unknown;
	error?: { code: number; message: string; data?: unknown };
};

type PendingCall = {
	resolve: (value: unknown) => void;
	reject: (err: Error) => void;
	timer: NodeJS.Timeout;
};

class McpClient {
	private proc: ChildProcessWithoutNullStreams | null = null;
	private nextId = 1;
	private buffer = "";
	private pending = new Map<number, PendingCall>();
	private initPromise: Promise<void> | null = null;

	private parseInvocation(): [string, string[]] {
		const tokens = HIPPOCAMPUS_MCP.trim().split(/\s+/).filter(Boolean);
		if (tokens.length === 0) {
			return ["hippocampus-mcp", []];
		}
		return [tokens[0], tokens.slice(1)];
	}

	private spawnIfNeeded(): void {
		if (this.proc && !this.proc.killed && this.proc.exitCode === null) {
			return;
		}
		const [cmd, args] = this.parseInvocation();
		this.proc = spawn(cmd, args, {
			stdio: ["pipe", "pipe", "pipe"],
			env: {
				...process.env,
				HIPPOCAMPUS_CLIENT,
			},
		}) as ChildProcessWithoutNullStreams;

		this.proc.stdout.setEncoding("utf8");
		this.proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
		this.proc.stderr.on("data", () => {
			// MCP servers log to stderr; we don't surface those in the TUI to
			// avoid noise. Errors are still reported through JSON-RPC.
		});
		this.proc.on("exit", (code) => {
			const err = new Error(`hippocampus-mcp exited (code=${code})`);
			for (const [, p] of this.pending) {
				clearTimeout(p.timer);
				p.reject(err);
			}
			this.pending.clear();
			this.proc = null;
			this.initPromise = null;
		});
	}

	private onStdout(chunk: string): void {
		this.buffer += chunk;
		let nlIdx = this.buffer.indexOf("\n");
		while (nlIdx !== -1) {
			const line = this.buffer.slice(0, nlIdx).trim();
			this.buffer = this.buffer.slice(nlIdx + 1);
			if (line) {
				try {
					const msg = JSON.parse(line) as JsonRpcResponse;
					if (typeof msg.id === "number") {
						const pending = this.pending.get(msg.id);
						if (pending) {
							this.pending.delete(msg.id);
							clearTimeout(pending.timer);
							if (msg.error) {
								pending.reject(new Error(msg.error.message));
							} else {
								pending.resolve(msg.result);
							}
						}
					}
					// notifications (no id) are ignored — we only care about responses
				} catch {
					// Malformed line — ignore; the MCP server may interleave
					// non-JSON output during startup on some setups.
				}
			}
			nlIdx = this.buffer.indexOf("\n");
		}
	}

	private send(method: string, params?: unknown): Promise<unknown> {
		this.spawnIfNeeded();
		const id = this.nextId++;
		const payload: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
		const line = `${JSON.stringify(payload)}\n`;
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				this.pending.delete(id);
				reject(new Error(`hippocampus MCP call '${method}' timed out`));
			}, TOOL_CALL_TIMEOUT_MS);
			this.pending.set(id, { resolve, reject, timer });
			if (!this.proc) {
				clearTimeout(timer);
				this.pending.delete(id);
				reject(new Error("hippocampus-mcp child not running"));
				return;
			}
			this.proc.stdin.write(line, (err) => {
				if (err) {
					clearTimeout(timer);
					this.pending.delete(id);
					reject(err);
				}
			});
		});
	}

	private sendNotification(method: string, params?: unknown): void {
		this.spawnIfNeeded();
		const payload = { jsonrpc: "2.0", method, params };
		if (this.proc) {
			this.proc.stdin.write(`${JSON.stringify(payload)}\n`);
		}
	}

	private ensureInitialized(): Promise<void> {
		if (this.initPromise) return this.initPromise;
		this.initPromise = (async () => {
			await this.send("initialize", {
				protocolVersion: "2024-11-05",
				capabilities: {},
				clientInfo: { name: "hippocampus-pi-extension", version: "1.0.0" },
			});
			this.sendNotification("notifications/initialized");
		})();
		return this.initPromise;
	}

	async callTool(name: string, args: Record<string, unknown>): Promise<{ text: string; raw: unknown }> {
		await this.ensureInitialized();
		const result = (await this.send("tools/call", { name, arguments: args })) as {
			content?: Array<{ type: string; text?: string }>;
			isError?: boolean;
		};
		const parts = result?.content ?? [];
		const text = parts
			.map((p) => (p.type === "text" && typeof p.text === "string" ? p.text : ""))
			.filter(Boolean)
			.join("\n");
		if (result?.isError) {
			throw new Error(text || `hippocampus tool '${name}' returned an error`);
		}
		return { text, raw: result };
	}

	shutdown(): void {
		for (const [, p] of this.pending) {
			clearTimeout(p.timer);
			p.reject(new Error("hippocampus extension shutting down"));
		}
		this.pending.clear();
		if (this.proc && this.proc.exitCode === null) {
			try {
				this.proc.stdin.end();
			} catch {
				/* ignore */
			}
			this.proc.kill();
		}
		this.proc = null;
		this.initPromise = null;
	}
}

// ---------------------------------------------------------------------------
// Hippocampus CLI helpers — used for lifecycle wiring
// ---------------------------------------------------------------------------

function hippoExec(
	args: string[],
	stdinPayload?: string,
	extraEnv: Record<string, string> = {},
): { ok: boolean; stdout: string; stderr: string } {
	try {
		const result = spawnSync(HIPPO_BIN, args, {
			input: stdinPayload,
			encoding: "utf8",
			timeout: 8_000,
			env: {
				...process.env,
				HIPPOCAMPUS_CLIENT,
				HIPPOCAMPUS_CWD: process.env.HIPPOCAMPUS_CWD || process.cwd(),
				HIPPOCAMPUS_TTY: process.env.HIPPOCAMPUS_TTY || process.env.TTY || process.env.SSH_TTY || "",
				...extraEnv,
			},
		});
		return {
			ok: result.status === 0,
			stdout: result.stdout ?? "",
			stderr: result.stderr ?? "",
		};
	} catch {
		return { ok: false, stdout: "", stderr: "" };
	}
}

function hippoContext(query: string | undefined, event: string): string {
	const args = ["context", "--client", HIPPOCAMPUS_CLIENT, "--event", event];
	if (query && query.trim()) {
		args.push("--query", query.trim().slice(0, ASK_TRUNCATE_CHARS));
	}
	const { ok, stdout } = hippoExec(args);
	return ok ? stdout.trim() : "";
}

// ---------------------------------------------------------------------------
// Tool schemas — loaded from tools.json, which `hippo register` generates
// from src/hippocampus/mcp/server.py TOOL_SPECS. No hand-mirrored list.
// ---------------------------------------------------------------------------

interface ToolManifestEntry {
	name: string;
	label: string;
	description: string;
	inputSchema: Record<string, unknown>;
}

function loadToolManifest(): ToolManifestEntry[] {
	const path = fileURLToPath(new URL("./tools.json", import.meta.url));
	const raw = readFileSync(path, "utf-8");
	const parsed = JSON.parse(raw) as ToolManifestEntry[];
	if (!Array.isArray(parsed) || parsed.length === 0) {
		throw new Error(`hippocampus: ${path} is empty — re-run 'hippo register'`);
	}
	return parsed;
}

const TOOL_MANIFEST = loadToolManifest();
type ToolName = string;

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default function hippocampus(pi: ExtensionAPI) {
	const mcp = new McpClient();

	function registerHippocampusTool(entry: ToolManifestEntry) {
		const name: ToolName = entry.name;
		const schema = Type.Unsafe<Record<string, unknown>>(entry.inputSchema) as TSchema;
		pi.registerTool({
			name,
			label: entry.label,
			description: entry.description,
			parameters: schema,
			async execute(_toolCallId, params) {
				try {
					const args = (params ?? {}) as Record<string, unknown>;
					const { text } = await mcp.callTool(name, args);
					return {
						content: [{ type: "text", text: text || "{}" }],
						details: { tool: name, args },
					};
				} catch (err) {
					const message = err instanceof Error ? err.message : String(err);
					return {
						content: [{ type: "text", text: `hippocampus tool '${name}' failed: ${message}` }],
						details: { tool: name, error: message },
					};
				}
			},
		});
	}

	TOOL_MANIFEST.forEach((entry) => registerHippocampusTool(entry));

	// -----------------------------------------------------------------------
	// Lifecycle hooks
	// -----------------------------------------------------------------------

	pi.on("session_start", async () => {
		hippoExec(["session", "start", "--client", HIPPOCAMPUS_CLIENT]);
	});

	pi.on("before_agent_start", async (event) => {
		const promptRaw = (event as { prompt?: string }).prompt ?? "";
		const prompt = promptRaw.trim();
		if (prompt) {
			const promptLogged = hippoExec(
				["transcript", "log", "user", "--client", HIPPOCAMPUS_CLIENT, "--source-event", "BeforeAgentStart", "--stdin"],
				prompt,
			).ok;
			hippoExec(
				["progress", "log", "ask", prompt.slice(0, ASK_TRUNCATE_CHARS), "--client", HIPPOCAMPUS_CLIENT],
				undefined,
				promptLogged ? { HIPPOCAMPUS_TRANSCRIPT_PROMPT_LOGGED: "1" } : {},
			);
			hippoExec(
				["autoremember", "--client", HIPPOCAMPUS_CLIENT, "--stdin", "--quiet"],
				prompt,
			);
		}

		const context = hippoContext(prompt, "BeforeAgentStart").slice(0, CONTEXT_BUDGET_CHARS);
		if (!context) return undefined;

		const current = (event as { systemPrompt?: string }).systemPrompt ?? "";
		return {
			systemPrompt: `${current}\n\n${context}`.trim(),
		};
	});

	pi.on("session_shutdown", async () => {
		mcp.shutdown();
	});

	pi.registerCommand("hippocampus", {
		description: "Inspect Hippocampus memory state from inside Pi",
		handler: async (_args, ctx) => {
			const { ok, stdout, stderr } = hippoExec(["doctor"]);
			const body = ok ? stdout : stderr || stdout || "hippo doctor failed";
			ctx.ui.notify(body.split("\n").slice(0, 4).join(" · "), ok ? "info" : "warning");
		},
	});
}

// Suppress unused-import noise when typecheckers run over this extension out
// of context. The `Static` helper is the canonical way to derive the param
// type for downstream consumers, even when we keep `execute()` loosely typed
// to allow forwarding to MCP without per-tool boilerplate.
export type ToolParams<T extends TSchema> = Static<T>;
