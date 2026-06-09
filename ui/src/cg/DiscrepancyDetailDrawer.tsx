import type { FieldValidation, PipelineRun } from "../api/types";

export interface DiscrepancySelection {
  field: FieldValidation;
  run: PipelineRun;
}

function confidenceClass(confidence: number | null) {
  if (confidence === null || confidence < 0.6) return "bg-red-100 text-red-800";
  if (confidence < 0.85) return "bg-yellow-100 text-yellow-800";
  return "bg-green-100 text-green-800";
}

export function DiscrepancyDetailDrawer({
  selection,
  onClose
}: {
  selection: DiscrepancySelection | null;
  onClose: () => void;
}) {
  const extractedField = selection?.run.extraction_result?.fields[selection.field.field_name];
  const confidence = extractedField?.is_present ? extractedField.confidence : null;
  const documentType = selection?.run.extraction_result?.document_type ?? "UNKNOWN";

  return (
    <div
      className={`fixed inset-0 z-40 ${selection ? "pointer-events-auto" : "pointer-events-none"}`}
      aria-hidden={!selection}
    >
      <div
        className={`absolute inset-0 bg-black/20 transition-opacity ${
          selection ? "opacity-100" : "opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`absolute right-0 top-0 h-full w-full max-w-xl overflow-y-auto bg-white p-6 shadow-2xl transition-transform ${
          selection ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Discrepancy detail
            </p>
            <h2 className="mt-1 text-xl font-bold">{selection?.field.field_name}</h2>
            <p className="mt-1 text-sm text-neutral-500">Document: {documentType}</p>
          </div>
          <button
            className="rounded-md border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-50"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        {selection ? (
          <div className="space-y-4 text-sm">
            <div className="rounded-lg border border-red-200 bg-red-50 p-4">
              <p className="text-xs font-semibold uppercase text-red-700">Found value</p>
              <p className="mt-1 text-lg font-semibold text-red-950">
                {selection.field.found_value ?? "Not present"}
              </p>
            </div>

            <div className="rounded-lg border border-neutral-200 p-4">
              <p className="text-xs font-semibold uppercase text-neutral-500">
                Expected value / rule
              </p>
              <p className="mt-1 text-neutral-900">
                {selection.field.expected_value ?? selection.field.expected_rule}
              </p>
              <p className="mt-2 text-xs text-neutral-600">{selection.field.reason}</p>
            </div>

            <div className="rounded-lg border border-neutral-200 p-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase text-neutral-500">
                  Extraction confidence
                </p>
                <span className={`rounded-full px-2 py-1 text-xs ${confidenceClass(confidence)}`}>
                  {confidence === null ? "missing" : `${Math.round(confidence * 100)}%`}
                </span>
              </div>
              <p className="text-xs text-neutral-500">Source snippet</p>
              <p className="mt-2 whitespace-pre-wrap rounded bg-neutral-50 p-3 font-mono text-xs">
                {extractedField?.source_snippet || "No source snippet available."}
              </p>
            </div>
          </div>
        ) : null}
      </aside>
    </div>
  );
}
