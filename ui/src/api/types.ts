export type DocumentType = "BOL" | "INVOICE" | "PACKING_LIST" | "COO" | "UNKNOWN";
export type FieldValidationStatus = "MATCH" | "MISMATCH" | "UNCERTAIN" | "MISSING";
export type ValidationOverallStatus = "PASSED" | "FAILED" | "NEEDS_REVIEW";
export type DecisionType = "AUTO_APPROVE" | "HUMAN_REVIEW" | "AMEND";
export type PipelineRunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "NEEDS_REVIEW";
export type StageName = "INGESTION" | "EXTRACTION" | "VALIDATION" | "ROUTING" | "STORAGE" | "QUERY";
export type StageStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";

export interface CustomerSummary {
  customer_id: string;
  name: string;
  rule_set_path: string;
  version: string;
}

export interface ExtractedField {
  name: string;
  value: string | null;
  confidence: number;
  source_page: number;
  source_snippet: string;
  reasoning: string;
  is_present: boolean;
}

export interface ExtractionResult {
  document_id: string;
  document_type: DocumentType;
  fields: Record<string, ExtractedField>;
  model_used: string;
  latency_ms: number;
  cost_usd: number;
  raw_response_id: string;
}

export interface FieldValidation {
  field_name: string;
  status: FieldValidationStatus;
  found_value: string | null;
  expected_value: string | null;
  expected_rule: string;
  reason: string;
  extraction_confidence?: number | null;
}

export interface ValidationResult {
  extraction_id: string;
  customer_id: string;
  rule_set_version: string;
  field_results: FieldValidation[];
  overall_status: ValidationOverallStatus;
  validator_confidence: number;
}

export interface RouterDecision {
  decision: DecisionType;
  reasoning: string;
  drafted_message: string | null;
  risk_flags: string[];
}

export interface StageEvent {
  stage: StageName;
  status: StageStatus;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  cost_usd: number;
  trace_id: string | null;
  message: string | null;
  error_message: string | null;
}

export interface PipelineRun {
  run_id: string;
  document_id: string;
  customer_id: string;
  status: PipelineRunStatus;
  stages: StageEvent[];
  started_at: string;
  completed_at: string | null;
  cost_total_usd: number;
  trace_id: string;
  extraction_result?: ExtractionResult | null;
  validation_result?: ValidationResult | null;
  router_decision?: RouterDecision | null;
}

export interface QueryEvidenceItem {
  tool_name: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface QueryEvidence {
  tool_calls: QueryEvidenceItem[];
}

export interface QueryAnswer {
  answer: string;
  evidence: QueryEvidence;
}

export const REQUIRED_FIELDS = [
  "consignee_name",
  "hs_code",
  "port_of_loading",
  "port_of_discharge",
  "incoterms",
  "description_of_goods",
  "gross_weight",
  "invoice_number"
] as const;
