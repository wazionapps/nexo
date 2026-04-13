/**
 * Tool definitions for the NEXO Brain OpenClaw memory plugin.
 *
 * Exposes cognitive memory tools as native OpenClaw tools via TypeBox schemas.
 * Each tool maps to a NEXO MCP tool via the MCP bridge.
 */
import { type TObject } from "@sinclair/typebox";
export interface ToolDef {
    name: string;
    nexoName: string;
    label: string;
    description: string;
    parameters: TObject;
}
export declare const COGNITIVE_TOOLS: ToolDef[];
export declare const ALL_TOOL_NAMES: string[];
