export type NodeKind = "module" | "function" | "class" | "route" | "model" | "table" | "external";
export type EdgeKind = "parsed" | "observed" | "inferred";
export type EdgeType = "calls" | "imports" | "reads" | "writes" | "depends_on" | "http_call";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  name: string;
  qualname: string;
  file?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  meta: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: EdgeType;
  kind: EdgeKind;
  meta: Record<string, unknown>;
}

export interface Route {
  id: string;
  method: string;
  path: string;
  handler_node_id: string;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  routes: Route[];
  meta: Record<string, unknown>;
}

export interface ScenarioStep {
  node_id: string;
  edge_id?: string | null;
  depth: number;
  order: number;
}

export interface Scenario {
  route: Route;
  steps: ScenarioStep[];
  tables_read: string[];
  tables_written: string[];
}

export interface SourcePayload {
  node_id: string;
  qualname: string;
  file?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  code?: string | null;
  incoming: GraphEdge[];
  outgoing: GraphEdge[];
}
