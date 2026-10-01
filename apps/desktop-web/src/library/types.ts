export type Column = {name: string; type: string; nullable: boolean; default_value: any};

export type Table = {
  id: string;
  path: string;
  name: string;
  owner?: string;
  table_revision?: number;
  schema_revision?: number;
  format_version?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  columns?: Column[];
};

export type Row = {
  _row_id: string;
  _revision: number;
  _created_at: string;
  _updated_at: string;
  _deleted_at: string | null;
  values: Record<string, any>;
};

export type Attachment = {
  attachment_id: string;
  row_id: string;
  filename: string;
  media_type: string;
  size: number;
  created_at: string;
  metadata: Record<string, any>;
};

export type Job = {
  job_id: string;
  owner: string;
  table_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at?: string;
  updated_at?: string;
  error?: string;
};

export type IndexStatusState = 'ready' | 'stale' | 'not_indexed' | 'queued' | 'indexing' | 'failed' | 'empty';

export type IndexStatus = {
  table_id: string;
  state: IndexStatusState;
  row_count: number;
  embedded_rows: number;
  missing_rows: number;
  pending_rows: number;
  chunk_count: number;
  last_job: Job | null;
};

export type SearchHit = {
  table_id: string;
  row_id: string;
  row_revision: number;
  path: string;
  score: number;
  score_type: string;
  snippet: string;
  approximate?: boolean;
  indexed_revision?: number;
  matched_chunks?: Array<{chunk_id: string; column: string}>;
  /** Present only for query results, which embed the full row. */
  values?: Record<string, any>;
};

export type SearchMode = 'fast' | 'quality' | 'structured';

export type SearchMeta = {
  partial?: boolean;
  missing_embeddings?: number;
  index_backend?: string;
  model_revision?: string;
  approximate?: boolean;
  [key: string]: any;
};

export type Theorem = {
  theorem_id: string;
  path: string;
  title: string;
  statement_natural?: string;
  assumptions?: string[];
  proof_natural?: string;
  formal_statement?: string;
  lean_source?: string;
  lean_version?: string;
  formal_status?: string;
  review_status?: string;
  confidence?: {evidence_score?: number; source_score?: number; consistency_score?: number};
  provenance?: string;
  depends_on?: string[];
  evidence_refs?: string[];
  revision?: number;
  created_at?: string;
  updated_at?: string;
};

export type QueryExplain = {
  path: string;
  ast: any;
  limit: number;
  offset: number;
  order_by: any;
  descending: boolean;
  execution: string;
  approximate: boolean;
};

export type RecentEntry = {kind: 'table' | 'row' | 'path' | 'theorem'; id: string; title: string; at: number};
