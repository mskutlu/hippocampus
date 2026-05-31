/**
 * Hippocampus extension for Pi (@earendil-works/pi-coding-agent).
 *
 * Pi deliberately ships without native MCP. This extension closes that gap:
 *
 *   1. Re-exposes all 13 Hippocampus MCP tools through `pi.registerTool()`
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

function hippoExec(args: string[], stdinPayload?: string): { ok: boolean; stdout: string; stderr: string } {
	try {
		const result = spawnSync(HIPPO_BIN, args, {
			input: stdinPayload,
			encoding: "utf8",
			timeout: 8_000,
			env: { ...process.env, HIPPOCAMPUS_CLIENT },
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
// Tool schemas — mirror src/hippocampus/mcp/server.py exactly
// ---------------------------------------------------------------------------

const ProgressKind = Type.Union([
	Type.Literal("goal"),
	Type.Literal("ask"),
	Type.Literal("done"),
	Type.Literal("blocker"),
	Type.Literal("decision"),
	Type.Literal("next"),
	Type.Literal("note"),
]);

const TOOL_SCHEMAS = {
	recall: Type.Object({
		query: Type.String({ description: "Free-text FTS query" }),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
		min_confidence: Type.Optional(Type.Number({ minimum: 0.0, maximum: 1.0 })),
		context_tag: Type.Optional(
			Type.String({ description: "Optional tag (e.g. 'debugging') added to every hit" }),
		),
	}),
	remember: Type.Object({
		content: Type.String({ description: "The synthesized fragment content" }),
		summary: Type.Optional(Type.String({ description: "One-line summary (optional; auto if blank)" })),
		tags: Type.Optional(Type.Array(Type.String())),
		source_type: Type.Optional(Type.String({ description: "e.g. 'session', 'decision', 'manual'" })),
		source_ref: Type.Optional(Type.String({ description: "Pointer to origin (path, URL, session id)" })),
		pinned: Type.Optional(Type.Boolean({ description: "Shield from decay" })),
	}),
	forget: Type.Object({
		fragment_id: Type.String(),
		reason: Type.Optional(Type.String()),
	}),
	pin: Type.Object({ fragment_id: Type.String() }),
	unpin: Type.Object({ fragment_id: Type.String() }),
	get_fragment: Type.Object({
		fragment_id: Type.String(),
		boost_on_read: Type.Optional(Type.Boolean()),
	}),
	list_fragments: Type.Object({
		tag: Type.Optional(Type.String()),
		min_confidence: Type.Optional(Type.Number()),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 200 })),
	}),
	top_fragments: Type.Object({
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
	}),
	get_stats: Type.Object({}),
	log_progress: Type.Object({
		kind: ProgressKind,
		content: Type.String({ description: "One-line synthesis of the event (not raw user text)." }),
		details: Type.Optional(Type.String({ description: "Optional longer context." })),
	}),
	get_progress: Type.Object({
		full: Type.Optional(Type.Boolean()),
		client: Type.Optional(Type.String()),
	}),
	end_progress: Type.Object({
		distill_to_fragment: Type.Optional(Type.Boolean()),
		summary: Type.Optional(Type.String()),
		tags: Type.Optional(Type.Array(Type.String())),
	}),
	undo_last_entry: Type.Object({
		client: Type.Optional(Type.String()),
	}),
} as const;

type ToolName = keyof typeof TOOL_SCHEMAS;

const TOOL_DESCRIPTIONS: Record<ToolName, string> = {
	recall:
		"Search synthesized memory fragments. Every returned fragment is boosted (+0.015 confidence, access counter +1, co-access associations strengthened, context_tag attached). Use this when you need to retrieve what you or the user already knows.",
	remember:
		"Store a synthesized fragment (NOT raw conversation). Only distilled, atomic ideas belong here. New fragments start at confidence=0.5.",
	forget:
		"Apply negative feedback to a fragment (-0.02 confidence). Use when a recalled fragment turns out to be wrong or stale.",
	pin: "Mark a fragment as pinned so it never decays.",
	unpin: "Remove the pinned flag from a fragment.",
	get_fragment: "Read a single fragment by id. Boosts confidence unless boost_on_read=false.",
	list_fragments: "Administrative listing (no boost). Filter by tag and/or minimum confidence.",
	top_fragments:
		"Return the top-N highest-ranking fragments (by confidence × recency). Used for auto-injection; does not apply boost.",
	get_stats: "Health dashboard: counts, average confidence, recent feedback events.",
	log_progress:
		"WORKING MEMORY — append one entry to the current session's ledger. Call this reflexively: every ask -> log_progress(kind='ask', ...); every completed action -> kind='done'; decisions -> 'decision'; blockers -> 'blocker'; planned next steps -> 'next'; goal changes -> 'goal'; other context -> 'note'. The entry survives compaction because the WORKING block is re-injected into the client's always-on rules file on every turn. Any frag_... ids referenced in content are boosted as if recalled. Dedup window: identical entries within 60s are merged.",
	get_progress:
		"Return the current session's ledger (or full history). Call this when you need more detail than the injected WORKING block shows.",
	end_progress:
		"Close the current session and optionally distill the whole ledger into a single long-term fragment. Call this when the task is complete. The next log_progress call will start a fresh session.",
	undo_last_entry:
		"Pop the most recent ledger entry from the current session. Use this to correct a log_progress mistake. Refuses if the entry is older than 5 minutes — use end_progress for older corrections.",
};

const TOOL_LABELS: Record<ToolName, string> = {
	recall: "Recall",
	remember: "Remember",
	forget: "Forget",
	pin: "Pin",
	unpin: "Unpin",
	get_fragment: "Get fragment",
	list_fragments: "List fragments",
	top_fragments: "Top fragments",
	get_stats: "Hippocampus stats",
	log_progress: "Log progress",
	get_progress: "Get progress",
	end_progress: "End progress",
	undo_last_entry: "Undo last entry",
};

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default function hippocampus(pi: ExtensionAPI) {
	const mcp = new McpClient();

	function registerHippocampusTool<TParams extends TSchema>(name: ToolName, schema: TParams) {
		pi.registerTool({
			name,
			label: TOOL_LABELS[name],
			description: TOOL_DESCRIPTIONS[name],
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

	(Object.keys(TOOL_SCHEMAS) as ToolName[]).forEach((name) => {
		registerHippocampusTool(name, TOOL_SCHEMAS[name] as TSchema);
	});

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
			hippoExec(
				["progress", "log", "ask", prompt.slice(0, ASK_TRUNCATE_CHARS), "--client", HIPPOCAMPUS_CLIENT],
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
