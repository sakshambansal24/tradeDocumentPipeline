import type { CustomerSummary, PipelineRun, QueryAnswer } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.message ?? payload.detail ?? `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getCustomers(): Promise<CustomerSummary[]> {
  return request<CustomerSummary[]>("/customers");
}

export async function createRun(input: {
  file: File;
  customerId: string;
  onUploadProgress?: (percent: number) => void;
}): Promise<PipelineRun> {
  const formData = new FormData();
  formData.append("file", input.file);
  formData.append("customer_id", input.customerId);
  input.onUploadProgress?.(20);
  const run = await request<PipelineRun>("/runs", {
    method: "POST",
    body: formData
  });
  input.onUploadProgress?.(100);
  return run;
}

export async function getRun(runId: string): Promise<PipelineRun> {
  return request<PipelineRun>(`/runs/${runId}`);
}

export async function askQuery(question: string): Promise<QueryAnswer> {
  return request<QueryAnswer>("/query", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ question })
  });
}
