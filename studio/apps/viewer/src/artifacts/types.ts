/** Shapes of saved analysis outputs (``analysis-output-v1``) and the bounded source that reads them. */
import type { Plain } from "../analysis/codec";
export type OutputKind = "number" | "table" | "distribution" | "figure" | "image";
export interface Field {
  name: string;
  /** ``int64``, ``float64``, ``string``, ``bool``, ``null`` or a nested description. */
  dtype: unknown;
  unit?: string | null;
  nullable?: boolean;
  observed_at?: unknown;
}
export interface DatasetSchema {
  name: string;
  fields: Field[];
  observation?: string;
}
export interface ArtifactDescriptor {
  path: string;
  length: number;
  sha256: string;
}
export interface MissingInput {
  run?: string;
  configuration?: number | null;
  index?: number;
  status?: string;
  missing_fields?: string[];
  reason?: string;
}
export interface OutputMetadata {
  partial_snapshot: boolean;
  planned: number;
  available: number;
  inferential_convention: string;
  input_availability: Record<string, { valid: number; missing: MissingInput[] }>;
  unit?: string | null;
  schema?: DatasetSchema;
  row_count?: number;
  media_type?: string;
  [key: string]: unknown;
}
export interface OutputDescriptor {
  id: string;
  name: string;
  kind: OutputKind;
  artifacts: ArtifactDescriptor[];
  metadata: OutputMetadata;
  renderer: { name?: string; version?: string } | null;
}
export interface OutputManifest {
  schema: "analysis-output-v1";
  execution_id: string;
  outputs: OutputDescriptor[];
  provenance: Record<string, unknown>;
}
export type Row = { [key: string]: Plain };
export interface RowsPage {
  rows: Row[];
  next_cursor: string | null;
  row_count: number;
  indexed: boolean;
}
/** Raw server query strings: ``sort=<field>:<asc|desc>`` and ``filter=<field>:<op>:<tagged-json>``. */
export interface TableQueryStrings {
  sort: string;
  filter: string;
}
export class ArtifactError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The server asked for a retry (an index is still being built). */
    readonly retry = false,
    readonly details: unknown = undefined,
  ) {
    super(message);
  }
}
export interface ArtifactSource {
  /** One page of a saved table; paging, sorting and filtering happen on the server. */
  page(outputId: string, cursor: string | null, limit: number, query?: TableQueryStrings): Promise<RowsPage>;
  /** One verified artifact file of an output. */
  blob(outputId: string, path: string): Promise<Blob>;
  /** The whole table as CSV. */
  csv(outputId: string): Promise<Blob>;
}
