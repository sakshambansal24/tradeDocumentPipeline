import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { createRun, getCustomers, getRun } from "./api/client";
import type { PipelineRun } from "./api/types";
import { DecisionCard } from "./components/DecisionCard";
import { ExtractionTable } from "./components/ExtractionTable";
import { QueryPanel } from "./components/QueryPanel";
import { RunTimeline } from "./components/RunTimeline";
import { UploadBar } from "./components/UploadBar";
import { ValidationTable } from "./components/ValidationTable";

export function App() {
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<PipelineRun | null>(null);

  const customersQuery = useQuery({
    queryKey: ["customers"],
    queryFn: getCustomers
  });

  useEffect(() => {
    const firstCustomer = customersQuery.data?.[0]?.customer_id;
    if (!selectedCustomerId && firstCustomer) setSelectedCustomerId(firstCustomer);
  }, [customersQuery.data, selectedCustomerId]);

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId ?? ""),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "RUNNING" || status === "PENDING" ? 1500 : false;
    }
  });

  const createRunMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile || !selectedCustomerId) throw new Error("Select a file and customer");
      setUploadProgress(0);
      return createRun({
        file: selectedFile,
        customerId: selectedCustomerId,
        onUploadProgress: setUploadProgress
      });
    },
    onSuccess: (run) => {
      setRunId(run.run_id);
      setLastRun(run);
      setUploadProgress(100);
    }
  });

  const visibleRun = runQuery.data ?? lastRun;
  const extractionIsPending =
    visibleRun?.status === "PENDING" || visibleRun?.status === "RUNNING";

  return (
    <main className="mx-auto max-w-7xl p-4 md:p-6">
      <header className="mb-6">
        <p className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          GoComet Nova POC
        </p>
        <h1 className="text-2xl font-bold">Trade-document pipeline</h1>
        <p className="mt-1 text-sm text-neutral-600">
          Upload one document, run extraction, validation, routing, persistence, and query the
          stored result.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <UploadBar
            customers={customersQuery.data ?? []}
            selectedCustomerId={selectedCustomerId}
            selectedFile={selectedFile}
            isRunning={createRunMutation.isPending}
            progress={uploadProgress}
            onCustomerChange={setSelectedCustomerId}
            onFileChange={setSelectedFile}
            onRun={() => createRunMutation.mutate()}
          />

          {createRunMutation.isError ? (
            <div className="rounded-md bg-red-100 p-3 text-sm text-red-800">
              {(createRunMutation.error as Error).message}
            </div>
          ) : null}

          <RunTimeline run={visibleRun ?? null} />
          <ExtractionTable
            extraction={visibleRun?.extraction_result}
            isPending={extractionIsPending}
          />
          <ValidationTable validation={visibleRun?.validation_result} />
          <DecisionCard decision={visibleRun?.router_decision} />
        </div>

        <QueryPanel />
      </div>
    </main>
  );
}
