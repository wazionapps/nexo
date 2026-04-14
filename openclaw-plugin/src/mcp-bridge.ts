/**
 * MCP Bridge — Communicates with the NEXO Brain MCP server via stdio JSON-RPC.
 *
 * Spawns the Python MCP server as a child process and sends/receives
 * JSON-RPC 2.0 messages over stdin/stdout.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";
import { homedir } from "node:os";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface NexoConfig {
  nexoHome: string;
  pythonPath: string;
}

export class McpBridge {
  private process: ChildProcess | null = null;
  private requestId = 0;
  private pending = new Map<number, {
    resolve: (value: unknown) => void;
    reject: (reason: Error) => void;
  }>();
  private buffer = "";
  private initialized = false;
  private config: NexoConfig;

  constructor(config: NexoConfig) {
    this.config = {
      nexoHome: config.nexoHome.replace("~", homedir()),
      pythonPath: config.pythonPath,
    };
  }

  async start(): Promise<void> {
    if (this.process) return;

    const serverPath = resolve(this.config.nexoHome, "server.py");

    const nodeProcess = globalThis.process;
    this.process = spawn(this.config.pythonPath, [serverPath], {
      env: {
        ...nodeProcess.env,
        NEXO_HOME: this.config.nexoHome,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.process.stdout!.on("data", (chunk: Buffer) => {
      this.buffer += chunk.toString();
      this.processBuffer();
    });

    this.process.stderr!.on("data", (chunk: Buffer) => {
      // MCP servers may log to stderr — ignore gracefully
    });

    this.process.on("exit", (code) => {
      this.process = null;
      this.initialized = false;
      for (const [, p] of this.pending) {
        p.reject(new Error(`NEXO MCP server exited with code ${code}`));
      }
      this.pending.clear();
    });

    // Initialize MCP protocol
    await this.send("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "openclaw-memory-nexo-brain", version: "5.4.2" },
    });

    await this.send("notifications/initialized", {});
    this.initialized = true;
  }

  async stop(): Promise<void> {
    if (this.process) {
      this.process.kill("SIGTERM");
      this.process = null;
      this.initialized = false;
    }
  }

  async callTool(name: string, args: Record<string, unknown> = {}): Promise<string> {
    if (!this.initialized) {
      await this.start();
    }

    const result = await this.send("tools/call", { name, arguments: args }) as {
      content?: Array<{ type: string; text?: string }>;
    };

    if (result?.content) {
      return result.content
        .filter((c) => c.type === "text" && c.text)
        .map((c) => c.text!)
        .join("\n");
    }

    return JSON.stringify(result);
  }

  async listTools(): Promise<Array<{ name: string; description: string; inputSchema: unknown }>> {
    if (!this.initialized) {
      await this.start();
    }

    const result = await this.send("tools/list", {}) as {
      tools?: Array<{ name: string; description: string; inputSchema: unknown }>;
    };

    return result?.tools ?? [];
  }

  private send(method: string, params?: Record<string, unknown>): Promise<unknown> {
    return new Promise((resolvePromise, reject) => {
      if (!this.process?.stdin) {
        reject(new Error("MCP server not running"));
        return;
      }

      const id = ++this.requestId;
      const request: JsonRpcRequest = {
        jsonrpc: "2.0",
        id,
        method,
        params,
      };

      this.pending.set(id, { resolve: resolvePromise, reject });

      const message = JSON.stringify(request);
      const header = `Content-Length: ${Buffer.byteLength(message)}\r\n\r\n`;
      this.process.stdin.write(header + message);

      // Timeout after 30s
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Timeout calling ${method}`));
        }
      }, 30000);
    });
  }

  private processBuffer(): void {
    while (true) {
      const headerEnd = this.buffer.indexOf("\r\n\r\n");
      if (headerEnd === -1) break;

      const header = this.buffer.slice(0, headerEnd);
      const match = header.match(/Content-Length:\s*(\d+)/i);
      if (!match) {
        this.buffer = this.buffer.slice(headerEnd + 4);
        continue;
      }

      const contentLength = parseInt(match[1], 10);
      const contentStart = headerEnd + 4;

      if (this.buffer.length < contentStart + contentLength) break;

      const content = this.buffer.slice(contentStart, contentStart + contentLength);
      this.buffer = this.buffer.slice(contentStart + contentLength);

      try {
        const response = JSON.parse(content) as JsonRpcResponse;
        if (response.id != null && this.pending.has(response.id)) {
          const p = this.pending.get(response.id)!;
          this.pending.delete(response.id);

          if (response.error) {
            p.reject(new Error(response.error.message));
          } else {
            p.resolve(response.result);
          }
        }
      } catch {
        // Malformed JSON — skip
      }
    }
  }
}
