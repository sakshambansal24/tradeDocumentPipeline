import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { confirmDraft, getShipment } from "../api/client";
import type {
  CrossFieldMatch,
  DecisionType,
  PipelineRun,
  Shipment,
  ShipmentEvent,
  ShipmentStatus
} from "../api/types";
import { ExtractionTable } from "../components/ExtractionTable";
import { ValidationTable } from "../components/ValidationTable";
import {
  DiscrepancyDetailDrawer,
  type DiscrepancySelection
} from "./DiscrepancyDetailDrawer";
import { useShipmentEvents } from "./useShipmentEvents";

function statusClass(status: ShipmentStatus) {
  if (status === "PROCESSING") return "animate-pulse bg-blue-100 text-blue-800";
  if (status === "REQUIRES_REVIEW") return "bg-yellow-100 text-yellow-800";
  if (status === "APPROVED") return "bg-green-100 text-green-800";
  if (status === "AMENDED") return "bg-orange-100 text-orange-800";
  return "bg-neutral-100 text-neutral-700";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function documentLabel(run: PipelineRun) {
  if (run.source_filename) {
    return run.source_filename.replace(/\.[^/.]+$/, "");
  }
  return run.extraction_result?.document_type ?? run.document_id.slice(0, 8);
}

export function ShipmentDetail() {
  const { shipment_id: shipmentId } = useParams();
  const queryClient = useQueryClient();
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [selection, setSelection] = useState<DiscrepancySelection | null>(null);
  const [bodyOverride, setBodyOverride] = useState<string | null>(null);
  const [subjectOverride, setSubjectOverride] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const shipmentQuery = useQuery({
    queryKey: ["shipment", shipmentId],
    queryFn: () => getShipment(shipmentId ?? ""),
    enabled: Boolean(shipmentId)
  });
  useShipmentEvents(
    useCallback(
      (event: ShipmentEvent) => {
        if (event.shipment_id === shipmentId) {
          queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] });
        }
        queryClient.invalidateQueries({ queryKey: ["shipments"] });
      },
      [queryClient, shipmentId]
    )
  );

  const shipment = shipmentQuery.data;
  const isReviewed = Boolean(shipment?.reply_message_id);
  const activeRun = useMemo(() => {
    if (!shipment) return null;
    return shipment.document_runs.find((run) => run.run_id === activeRunId) ?? shipment.document_runs[0] ?? null;
  }, [activeRunId, shipment]);
  const parsedDraft = parseDraft(shipment?.draft_reply ?? "");
  const draftSubject = subjectOverride ?? parsedDraft.subject;
  const draftBody = bodyOverride ?? parsedDraft.body;

  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!shipment) throw new Error("Shipment not loaded");
      if (shipment.reply_message_id) throw new Error("CG has already sent a reply for this shipment");
      return confirmDraft({
        shipmentId: shipment.shipment_id,
        draftReply: composeDraft(draftSubject, draftBody)
      });
    },
    onSuccess: () => {
      setShowConfirm(false);
      queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] });
      queryClient.invalidateQueries({ queryKey: ["shipments"] });
    }
  });
  if (shipmentQuery.isLoading) {
    return <main className="mx-auto max-w-7xl p-6 text-sm text-neutral-500">Loading shipment...</main>;
  }

  if (!shipment) {
    return (
      <main className="mx-auto max-w-7xl p-6">
        <Link className="text-sm font-medium text-blue-700" to="/cg">
          Back to inbox
        </Link>
        <p className="mt-4 text-sm text-red-700">Shipment not found.</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-7xl p-4 md:p-6">
      <div className="mb-4">
        <Link className="text-sm font-medium text-blue-700 hover:text-blue-900" to="/cg">
          Back to CG inbox
        </Link>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-4">
          <ShipmentSummary shipment={shipment} />
          <DocumentVerificationPanel
            activeRun={activeRun}
            shipment={shipment}
            onRunChange={setActiveRunId}
            onDiscrepancyClick={(run, field) => setSelection({ run, field })}
          />
          <CrossDocumentPanel shipment={shipment} />
        </div>

        <DraftReplyPanel
          agentDecision={shipment.overall_decision?.decision ?? null}
          body={draftBody}
          isVisible={Boolean(shipment.overall_decision)}
          isReviewed={isReviewed}
          onBodyChange={setBodyOverride}
          onRequestConfirm={() => setShowConfirm(true)}
          replyMailPath={shipment.reply_mail_path ?? null}
          replyMessageId={shipment.reply_message_id ?? null}
          onSubjectChange={setSubjectOverride}
          sender={shipment.triggered_by ?? "supplier@example.com"}
          subject={draftSubject}
        />
      </div>

      <DiscrepancyDetailDrawer selection={selection} onClose={() => setSelection(null)} />

      {showConfirm ? (
        <ConfirmationModal
          isPending={confirmMutation.isPending}
          error={confirmMutation.error as Error | null}
          onCancel={() => setShowConfirm(false)}
          onConfirm={() => confirmMutation.mutate()}
        />
      ) : null}
    </main>
  );
}

function ShipmentSummary({ shipment }: { shipment: Shipment }) {
  const fileLabels = shipment.document_runs.map(documentLabel);
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Shipment summary
          </p>
          <h1 className="mt-1 text-xl font-bold">{shipment.subject ?? shipment.email_id}</h1>
          <p className="mt-1 text-sm text-neutral-600">
            From {shipment.triggered_by ?? "Unknown sender"} · Received{" "}
            {formatTime(shipment.triggered_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${statusClass(shipment.status)}`}>
            {shipment.status}
          </span>
          <span className="rounded-full bg-neutral-100 px-3 py-1 text-xs font-semibold text-neutral-700">
            Trust: {trustScore(shipment)}%
          </span>
        </div>
      </div>
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-3">
        <SummaryStat label="Documents" value={String(shipment.document_runs.length)} />
        <SummaryStat label="Files" value={fileLabels.length ? fileLabels.join(", ") : "Pending"} />
        <SummaryStat label="Customer" value={shipment.customer_id} />
      </div>
      <div className="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs">
        <p className="font-semibold uppercase tracking-wide text-neutral-500">Mail thread</p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <ThreadFact label="To CG" value={shipment.recipient ?? "cg@gocomet.local"} />
          <ThreadFact label="Original Message-ID" value={shipment.original_message_id ?? "Legacy shipment"} />
          <ThreadFact label="Reply Message-ID" value={shipment.reply_message_id ?? "Not sent yet"} />
          <ThreadFact label="Local sent file" value={shipment.reply_mail_path ?? "Not sent yet"} />
        </div>
      </div>
    </section>
  );
}

function ThreadFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-neutral-500">{label}</p>
      <p className="mt-1 break-all font-mono text-neutral-800">{value}</p>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-neutral-50 p-3">
      <p className="text-xs uppercase tracking-wide text-neutral-500">{label}</p>
      <p className="mt-1 font-medium text-neutral-900">{value}</p>
    </div>
  );
}

function DocumentVerificationPanel({
  activeRun,
  shipment,
  onDiscrepancyClick,
  onRunChange
}: {
  activeRun: PipelineRun | null;
  shipment: Shipment;
  onDiscrepancyClick: (run: PipelineRun, field: DiscrepancySelection["field"]) => void;
  onRunChange: (runId: string) => void;
}) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold">Per-document verification results</h2>
      <div className="mt-3 flex flex-wrap gap-2">
        {shipment.document_runs.map((run) => {
          const label = documentLabel(run);
          const isActive = activeRun?.run_id === run.run_id;
          return (
            <button
              className={`rounded-full px-3 py-1 text-sm font-medium ${
                isActive ? "bg-blue-700 text-white" : "bg-neutral-100 text-neutral-700"
              }`}
              key={run.run_id}
              onClick={() => onRunChange(run.run_id)}
            >
              <span>{label}</span>
              <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${
                isActive ? "bg-white/20 text-white" : "bg-white text-neutral-500"
              }`}>
                {run.extraction_result?.document_type ?? "Pending"}
              </span>
            </button>
          );
        })}
      </div>
      {activeRun ? (
        <div className="mt-4 space-y-4">
          <ExtractionTable extraction={activeRun.extraction_result} />
          <ValidationContextPanel activeRun={activeRun} shipment={shipment} />
          <ValidationTable
            validation={activeRun.validation_result}
            onFieldClick={(field) => onDiscrepancyClick(activeRun, field)}
          />
        </div>
      ) : (
        <p className="mt-4 text-sm text-neutral-500">No document runs available yet.</p>
      )}
    </section>
  );
}

function CrossDocumentPanel({ shipment }: { shipment: Shipment }) {
  const result = shipment.cross_validation_result;
  if (!result) return null;
  const columns = Array.from(
    new Set(result.checked_fields.flatMap((field) => Object.keys(field.values_by_doc)))
  );
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Cross-document consistency</h2>
        <span
          className={`rounded-full px-2 py-1 text-xs font-semibold ${
            result.overall_consistent ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
          }`}
        >
          {result.overall_consistent ? "All checked fields align" : "Conflicts detected"}
        </span>
      </div>
      {!result.overall_consistent ? (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm font-medium text-red-900">
          Warning: Cross-document conflicts detected — this shipment cannot be auto-approved
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-neutral-200 text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="py-2 pr-4">Field</th>
              {columns.map((column) => (
                <th className="py-2 pr-4" key={column}>
                  {column} value
                </th>
              ))}
              <th className="py-2 pr-4">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {result.checked_fields.map((field) => (
              <CrossFieldRow columns={columns} field={field} key={field.field_name} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CrossFieldRow({ columns, field }: { columns: string[]; field: CrossFieldMatch }) {
  const inconsistent = field.status === "INCONSISTENT";
  const insufficient = field.status === "INSUFFICIENT_DATA";
  const rowClass = inconsistent
    ? "bg-red-50 text-red-950"
    : insufficient
      ? "bg-yellow-50 text-yellow-950"
      : undefined;
  const statusClassName = inconsistent
    ? "bg-red-100 text-red-800"
    : insufficient
      ? "bg-yellow-100 text-yellow-800"
      : "bg-green-100 text-green-800";
  return (
    <tr className={rowClass}>
      <td className="py-3 pr-4 font-medium">
        {inconsistent ? "⚠ " : insufficient ? "! " : ""}
        {field.field_name}
      </td>
      {columns.map((column) => (
        <td className="py-3 pr-4" key={column}>
          {field.values_by_doc[column] ?? "-"}
        </td>
      ))}
      <td className="py-3 pr-4">
        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${statusClassName}`}>
          {field.status}
        </span>
        <div className="mt-1 max-w-xs text-xs text-neutral-600">{field.reason}</div>
      </td>
    </tr>
  );
}

function ValidationContextPanel({
  activeRun,
  shipment
}: {
  activeRun: PipelineRun;
  shipment: Shipment;
}) {
  const activeMismatches =
    activeRun.validation_result?.field_results.filter((field) =>
      ["MISMATCH", "MISSING", "UNCERTAIN"].includes(field.status)
    ) ?? [];
  const crossFieldsByName = new Map(
    shipment.cross_validation_result?.checked_fields.map((field) => [field.field_name, field]) ?? []
  );

  if (activeMismatches.length === 0) {
    return (
      <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-900">
        No validation mismatches for {documentLabel(activeRun)}.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3">
      <h3 className="text-sm font-semibold text-red-950">
        Mismatch context across documents
      </h3>
      <div className="mt-3 space-y-3">
        {activeMismatches.map((field) => {
          const crossField = crossFieldsByName.get(field.field_name);
          return (
            <div className="rounded-md bg-white p-3 text-sm" key={`${activeRun.run_id}-${field.field_name}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{field.field_name}</span>
                <span className="rounded-full bg-red-100 px-2 py-1 text-xs font-semibold text-red-800">
                  {field.status}
                </span>
              </div>
              <p className="mt-2 text-neutral-700">{field.reason}</p>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                <ContextValue label="Found in selected doc" value={field.found_value ?? "Missing"} />
                <ContextValue label="Expected" value={field.expected_value ?? field.expected_rule} />
              </div>
              {crossField ? (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                    Values from shipment documents
                  </p>
                  <div className="mt-2 grid gap-2">
                    {Object.entries(crossField.values_by_doc).map(([label, value]) => (
                      <ContextValue key={label} label={label} value={value ?? "Missing"} />
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ContextValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2">
      <p className="text-xs uppercase tracking-wide text-neutral-500">{label}</p>
      <p className="mt-1 font-medium text-neutral-900">{value}</p>
    </div>
  );
}

function DraftReplyPanel({
  agentDecision,
  body,
  isReviewed,
  isVisible,
  onBodyChange,
  onRequestConfirm,
  replyMailPath,
  replyMessageId,
  onSubjectChange,
  sender,
  subject
}: {
  agentDecision: DecisionType | null;
  body: string;
  isReviewed: boolean;
  isVisible: boolean;
  onBodyChange: (value: string) => void;
  onRequestConfirm: () => void;
  replyMailPath: string | null;
  replyMessageId: string | null;
  onSubjectChange: (value: string) => void;
  sender: string;
  subject: string;
}) {
  if (!isVisible) {
    return (
      <section className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
        Waiting for the agent recommendation and draft reply.
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{isReviewed ? "Sent CG reply" : "Draft reply"}</h2>
        {agentDecision ? (
          <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs font-semibold text-neutral-700">
            Agent recommendation: {agentDecision}
          </span>
        ) : null}
      </div>
      {isReviewed ? (
        <div className="mt-3 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-900">
          CG has already confirmed this reply. The message is locked to avoid sending duplicate
          responses on the same supplier thread.
        </div>
      ) : (
        <div className="mt-3 rounded-md border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-900">
          Review carefully before sending or approving. The agent&apos;s recommendation is advisory;
          CG makes the final decision.
        </div>
      )}
      <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
        This reply is drafted as part of the same supplier email thread using the original subject.
      </div>
      {isReviewed ? (
        <div className="mt-3 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-700">
          <p className="font-semibold uppercase tracking-wide text-neutral-500">Sent mail metadata</p>
          <p className="mt-2 break-all font-mono">Reply Message-ID: {replyMessageId}</p>
          <p className="mt-1 break-all font-mono">Saved .eml: {replyMailPath}</p>
        </div>
      ) : null}
      <div className="mt-4 space-y-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-neutral-700">To</span>
          <input
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            readOnly
            value={sender}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-neutral-700">Subject</span>
          <input
            className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2"
            onChange={(event) => onSubjectChange(event.target.value)}
            readOnly={isReviewed}
            value={subject}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-neutral-700">Body</span>
          <textarea
            className="min-h-[360px] w-full rounded-md border border-neutral-300 bg-stone-50 px-3 py-2 font-mono text-sm leading-6"
            onChange={(event) => onBodyChange(event.target.value)}
            readOnly={isReviewed}
            value={body}
          />
        </label>
      </div>
      {!isReviewed ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            className="rounded-md bg-orange-600 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-700"
            onClick={onRequestConfirm}
          >
            Confirm CG decision
          </button>
        </div>
      ) : null}
    </section>
  );
}

function ConfirmationModal({
  error,
  isPending,
  onCancel,
  onConfirm
}: {
  error: Error | null;
  isPending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4">
      <div className="max-w-md rounded-lg bg-white p-5 shadow-xl">
        <h2 className="text-lg font-bold">Confirm CG-reviewed reply</h2>
        <p className="mt-2 text-sm text-neutral-600">
          This writes a local same-thread reply email, records that CG reviewed the draft,
          and locks the reply to prevent duplicate sends.
        </p>
        {error ? <p className="mt-3 text-sm text-red-700">{error.message}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <button
            className="rounded-md border border-neutral-300 px-4 py-2 text-sm"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="rounded-md bg-orange-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
            disabled={isPending}
            onClick={onConfirm}
          >
            {isPending ? "Saving..." : "Confirm reviewed draft"}
          </button>
        </div>
      </div>
    </div>
  );
}

function trustScore(shipment: Shipment) {
  const confidences = shipment.document_runs.flatMap((run) =>
    Object.values(run.extraction_result?.fields ?? {})
      .filter((field) => field.is_present)
      .map((field) => field.confidence)
  );
  if (confidences.length === 0) return 0;
  const average = confidences.reduce((sum, confidence) => sum + confidence, 0) / confidences.length;
  return Math.round(average * 100);
}

function parseDraft(draft: string) {
  const lines = draft.split("\n");
  const firstLine = lines[0] ?? "";
  if (firstLine.toLowerCase().startsWith("subject:")) {
    return {
      subject: firstLine.replace(/^subject:\s*/i, ""),
      body: lines.slice(1).join("\n").trimStart()
    };
  }
  return {
    subject: "Amendment Required",
    body: draft
  };
}

function composeDraft(subject: string, body: string) {
  return `Subject: ${subject}\n\n${body}`;
}
