export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Dashboard {
  active_incidents: number;
  high_risk_operations: number;
  policy_denied_count: number;
  agent_success_rate: number;
  rca_accuracy: number | null;
  workflow_latency_ms: number | null;
}

export interface IncidentSummary {
  incident_id: string;
  status: string;
  risk: string | null;
  started_at: string | null;
  updated_at: string | null;
  run_count: number;
  span_count: number;
}

export interface TimelineItem {
  source: string;
  event_type: string;
  name: string;
  status: string;
  timestamp: string;
  latency_ms: number | null;
  error_code: string | null;
}

export interface IncidentDetail {
  incident_id: string;
  status: string;
  risk: string | null;
  current_stage: string | null;
  approval_status: string | null;
  execution_status: string | null;
  timeline: TimelineItem[];
  timeline_next_cursor: string | null;
}

export interface TraceItem {
  span_id: string;
  parent_id: string | null;
  node: string | null;
  agent: string | null;
  tool: string | null;
  status: string;
  latency_ms: number | null;
  timestamp: string;
  error_code: string | null;
}

export interface SecurityEvent {
  incident_id: string;
  actor_id: string | null;
  event_type: string;
  severity: string;
  decision: string | null;
  timestamp: string;
}

export interface PolicyDecision {
  incident_id: string;
  policy_id: string;
  decision: string;
  operation: string;
  risk_level: string;
  timestamp: string;
}

export interface EvaluationSummary {
  source: string;
  dataset_version: string;
  total_cases: number;
  passed_cases: number;
  rca_accuracy: number | null;
  top1_accuracy: number | null;
  top3_accuracy: number | null;
  created_at: string;
}

export interface EmbeddedTask {
  task_id: string;
  goal: string;
  status: string;
  validation_state: string | null;
  summary: string | null;
  error: string | null;
  final_report: string | null;
}

export interface EmbeddedArtifact {
  artifact_id: string;
  type: string;
  name: string;
  version: string;
  created_by: string;
  sha256: string;
  size_bytes: number;
}

export interface EmbeddedTaskDetail extends EmbeddedTask {
  artifacts: EmbeddedArtifact[];
  approval_request: {
    request_id: string;
    action: string;
    target: string;
    risk_level: string;
    artifact_ref: string | null;
    execution_plan: string[];
  } | null;
  firmware_filename: string | null;
}

export interface CapabilityInfo {
  name: string;
  version: string;
  type: string;
  permission: string;
  backend: string;
  enabled: boolean;
  metadata: Record<string, unknown>;
}
