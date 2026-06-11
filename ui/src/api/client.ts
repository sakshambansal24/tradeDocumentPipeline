import type {
  CustomerSummary,
  LocalMailDelivery,
  PipelineRun,
  QueryAnswer,
  Shipment,
  SimulatedMailRequest
} from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.message ?? payload.detail ?? `Request failed: ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
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

export async function getShipments(): Promise<Shipment[]> {
  return request<Shipment[]>("/shipments");
}

export async function getShipment(shipmentId: string): Promise<Shipment> {
  return request<Shipment>(`/shipments/${shipmentId}`);
}

export async function deleteShipment(shipmentId: string): Promise<void> {
  await request<void>(`/shipments/${shipmentId}`, {
    method: "DELETE"
  });
}

export async function simulateIncomingMail(mail: SimulatedMailRequest): Promise<LocalMailDelivery> {
  return request<LocalMailDelivery>("/mail/simulate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(mail)
  });
}

export async function simulateUploadedMail(input: {
  emailId?: string;
  sender: string;
  recipient: string;
  subject: string;
  customerId: string;
  body: string;
  inReplyTo?: string;
  attachments: Array<{ filename: string; file: File }>;
}): Promise<LocalMailDelivery> {
  const formData = new FormData();
  if (input.emailId) formData.append("email_id", input.emailId);
  if (input.inReplyTo?.trim()) formData.append("in_reply_to", input.inReplyTo.trim());
  formData.append("sender", input.sender);
  formData.append("recipient", input.recipient);
  formData.append("subject", input.subject);
  formData.append("customer_id", input.customerId);
  formData.append("body", input.body);
  input.attachments.forEach((attachment) => {
    formData.append("filenames", attachment.filename);
    formData.append("files", attachment.file, attachment.file.name);
  });
  return request<LocalMailDelivery>("/mail/simulate-upload", {
    method: "POST",
    body: formData
  });
}

export async function confirmDraft(input: {
  shipmentId: string;
  draftReply: string;
}): Promise<Shipment> {
  return request<Shipment>(`/shipments/${input.shipmentId}/confirm-draft`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ draft_reply: input.draftReply })
  });
}
