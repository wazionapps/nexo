/**
 * MCP Bridge — Communicates with the NEXO Brain MCP server via stdio JSON-RPC.
 *
 * Spawns the Python MCP server as a child process and sends/receives
 * JSON-RPC 2.0 messages over stdin/stdout.
 */
export interface NexoConfig {
    nexoHome: string;
    pythonPath: string;
}
export declare class McpBridge {
    private process;
    private requestId;
    private pending;
    private buffer;
    private initialized;
    private config;
    constructor(config: NexoConfig);
    start(): Promise<void>;
    stop(): Promise<void>;
    callTool(name: string, args?: Record<string, unknown>): Promise<string>;
    listTools(): Promise<Array<{
        name: string;
        description: string;
        inputSchema: unknown;
    }>>;
    private send;
    private processBuffer;
}
