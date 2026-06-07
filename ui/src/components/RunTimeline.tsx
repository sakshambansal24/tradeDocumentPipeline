import type { PipelineRun, StageName, StageStatus } from "../api/types";

const STAGES: StageName[] = ["INGESTION", "EXTRACTION", "VALIDATION", "ROUTING", "STORAGE"];

const LABELS: Record<StageName, string> = {
  INGESTION: "Ingest",
  EXTRACTION: "Extract",
  VALIDATION: "Validate",
  ROUTING: "Route",
  STORAGE: "Persist",
  QUERY: "Query"
};

function stageStatus(run: PipelineRun | null, stage: StageName): StageStatus | "PENDING" | "SKIPPED" {
  const event = run?.stages.find((item) => item.stage === stage);
  if (event) return event.status;

  // Validation can be skipped when quality gate triggers early handoff
  if (stage === "VALIDATION" && run) {
    const hasExtraction = run.stages.some((s) => s.stage === "EXTRACTION");
    const hasRouting = run.stages.some((s) => s.stage === "ROUTING");
    if (hasExtraction && hasRouting) {
      return "SKIPPED";  // Extraction completed, routing happened, validation was skipped
    }
  }

  return "PENDING";
}

function pillClass(status: StageStatus | "PENDING" | "SKIPPED") {
  if (status === "COMPLETED") return "bg-green-100 text-green-800";
  if (status === "FAILED") return "bg-red-100 text-red-800";
  if (status === "RUNNING") return "bg-yellow-100 text-yellow-800";
  if (status === "SKIPPED") return "bg-blue-100 text-blue-800";
  return "bg-neutral-200 text-neutral-700";
}

export function RunTimeline({ run }: { run: PipelineRun | null }) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold">Run timeline</h2>
        {run ? <span className="text-xs text-neutral-500">Run {run.run_id}</span> : null}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {STAGES.map((stage) => {
          const status = stageStatus(run, stage);
          return (
            <div key={stage} className="rounded-md border border-neutral-200 p-3">
              <div className="text-sm font-medium">{LABELS[stage]}</div>
              <span className={`mt-2 inline-flex rounded-full px-2 py-1 text-xs ${pillClass(status)}`}>
                {status.toLowerCase()}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
