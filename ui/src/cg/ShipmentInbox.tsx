import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { Link } from "react-router-dom";
import { deleteShipment, getCustomers, getShipments } from "../api/client";
import type { Shipment, ShipmentStatus } from "../api/types";
import { InboxSimulator } from "./InboxSimulator";
import { useShipmentEvents } from "./useShipmentEvents";

function statusClass(status: ShipmentStatus) {
  if (status === "PROCESSING") return "animate-pulse bg-blue-100 text-blue-800";
  if (status === "REQUIRES_REVIEW") return "bg-yellow-100 text-yellow-800";
  if (status === "APPROVED") return "bg-green-100 text-green-800";
  if (status === "AMENDED") return "bg-orange-100 text-orange-800";
  return "bg-neutral-100 text-neutral-700";
}

function reviewState(shipment: Shipment) {
  if (shipment.reply_message_id) {
    return {
      label: "Reviewed",
      detail: "CG reply sent",
      className: "bg-green-100 text-green-800"
    };
  }
  if (shipment.status === "PROCESSING" || shipment.status === "PENDING") {
    return {
      label: "Processing",
      detail: "Pipeline running",
      className: "animate-pulse bg-blue-100 text-blue-800"
    };
  }
  return {
    label: "Needs CG review",
    detail: "Draft waiting",
    className: "bg-red-100 text-red-800"
  };
}

function shortId(id: string) {
  return id.slice(0, 8);
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

export function ShipmentInbox() {
  const queryClient = useQueryClient();
  const shipmentsQuery = useQuery({
    queryKey: ["shipments"],
    queryFn: getShipments
  });
  useShipmentEvents(
    useCallback(() => {
      queryClient.invalidateQueries({ queryKey: ["shipments"] });
    }, [queryClient])
  );
  const customersQuery = useQuery({
    queryKey: ["customers"],
    queryFn: getCustomers
  });
  const customersById = new Map(
    (customersQuery.data ?? []).map((customer) => [customer.customer_id, customer.name])
  );
  const deleteMutation = useMutation({
    mutationFn: deleteShipment,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["shipments"] });
    }
  });
  const shipments = shipmentsQuery.data ?? [];
  const needsReviewShipments = shipments.filter(
    (shipment) =>
      !shipment.reply_message_id &&
      shipment.status !== "PROCESSING" &&
      shipment.status !== "PENDING"
  );
  const processingShipments = shipments.filter(
    (shipment) =>
      !shipment.reply_message_id &&
      (shipment.status === "PROCESSING" || shipment.status === "PENDING")
  );
  const reviewedShipments = shipments.filter((shipment) => shipment.reply_message_id);

  return (
    <main className="mx-auto max-w-7xl p-4 md:p-6">
      <header className="mb-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            CG workflow
          </p>
          <h1 className="text-2xl font-bold">Shipment verification inbox</h1>
          <p className="mt-1 text-sm text-neutral-600">
            Review incoming SU emails, cross-document checks, and amendment drafts.
          </p>
        </div>
        <Link className="text-sm font-medium text-blue-700 hover:text-blue-900" to="/">
          Back to Part 1 upload
        </Link>
      </header>

      <div className="mb-5">
        <InboxSimulator customers={customersQuery.data ?? []} />
      </div>

      <div className="mb-5 grid gap-3 md:grid-cols-3">
        <InboxMetric
          label="Needs CG review"
          value={String(needsReviewShipments.length)}
          tone="border-red-200 bg-red-50 text-red-900"
        />
        <InboxMetric
          label="Processing"
          value={String(processingShipments.length)}
          tone="border-blue-200 bg-blue-50 text-blue-900"
        />
        <InboxMetric
          label="Already reviewed"
          value={String(reviewedShipments.length)}
          tone="border-green-200 bg-green-50 text-green-900"
        />
      </div>

      <section className="rounded-lg border border-neutral-200 bg-white shadow-sm">
        <div className="border-b border-neutral-200 p-4">
          <h2 className="text-base font-semibold">CG mailbox shipments</h2>
          <p className="text-sm text-neutral-500">
            Local MIME emails parsed by the mail listener. Updates live from backend events.
          </p>
        </div>
        {shipmentsQuery.isLoading ? (
          <p className="p-4 text-sm text-neutral-500">Loading shipments...</p>
        ) : shipments.length === 0 ? (
          <p className="p-4 text-sm text-neutral-500">No shipments yet. Use the simulator above.</p>
        ) : (
          <div className="space-y-5 p-4">
            <ShipmentSection
              title="Needs CG review"
              description="Pipeline is done and an editable reply draft is waiting for CG action."
              shipments={needsReviewShipments}
              customersById={customersById}
              deletingShipmentId={deleteMutation.variables ?? null}
              emptyText="No shipments are waiting for CG review."
              onDelete={(shipmentId) => deleteMutation.mutate(shipmentId)}
              accentClass="border-red-200"
            />
            <ShipmentSection
              title="Processing"
              description="Mail listener has picked up the email and document agents are still running."
              shipments={processingShipments}
              customersById={customersById}
              deletingShipmentId={deleteMutation.variables ?? null}
              emptyText="No shipments are processing."
              onDelete={(shipmentId) => deleteMutation.mutate(shipmentId)}
              accentClass="border-blue-200"
            />
            <ShipmentSection
              title="Already reviewed"
              description="CG has confirmed a reply, and a threaded .eml response was written to sent mail."
              shipments={reviewedShipments}
              customersById={customersById}
              deletingShipmentId={deleteMutation.variables ?? null}
              emptyText="No shipments have been reviewed yet."
              onDelete={(shipmentId) => deleteMutation.mutate(shipmentId)}
              accentClass="border-green-200"
            />
          </div>
        )}
      </section>
    </main>
  );
}

function InboxMetric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className={`rounded-lg border p-4 shadow-sm ${tone}`}>
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-2 text-3xl font-bold">{value}</p>
    </div>
  );
}

function ShipmentSection({
  title,
  description,
  shipments,
  customersById,
  deletingShipmentId,
  emptyText,
  onDelete,
  accentClass
}: {
  title: string;
  description: string;
  shipments: Shipment[];
  customersById: Map<string, string>;
  deletingShipmentId: string | null;
  emptyText: string;
  onDelete: (shipmentId: string) => void;
  accentClass: string;
}) {
  return (
    <section className={`rounded-lg border ${accentClass}`}>
      <div className="border-b border-neutral-200 bg-neutral-50 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">{title}</h3>
            <p className="text-sm text-neutral-500">{description}</p>
          </div>
          <span className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-neutral-700">
            {shipments.length}
          </span>
        </div>
      </div>
      {shipments.length === 0 ? (
        <p className="px-4 py-5 text-sm text-neutral-500">{emptyText}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-neutral-200 text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-neutral-500">
                <th className="px-4 py-3">Shipment</th>
                <th className="px-4 py-3">From</th>
                <th className="px-4 py-3">Subject</th>
                <th className="px-4 py-3">Docs</th>
                <th className="px-4 py-3">Received</th>
                <th className="px-4 py-3">CG state</th>
                <th className="px-4 py-3">Agent recommendation</th>
                <th className="px-4 py-3">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {shipments.map((shipment) => (
                <ShipmentRow
                  key={shipment.shipment_id}
                  shipment={shipment}
                  customerName={customersById.get(shipment.customer_id) ?? shipment.customer_id}
                  isDeleting={deletingShipmentId === shipment.shipment_id}
                  onDelete={onDelete}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ShipmentRow({
  shipment,
  customerName,
  isDeleting,
  onDelete
}: {
  shipment: Shipment;
  customerName: string;
  isDeleting: boolean;
  onDelete: (shipmentId: string) => void;
}) {
  const state = reviewState(shipment);
  const handleDelete = () => {
    if (window.confirm(`Delete shipment ${shortId(shipment.shipment_id)} from the CG inbox?`)) {
      onDelete(shipment.shipment_id);
    }
  };
  return (
    <tr className={!shipment.reply_message_id && shipment.status !== "PROCESSING" ? "bg-red-50/40 hover:bg-red-50" : "hover:bg-neutral-50"}>
      <td className="px-4 py-3 font-medium">
        <Link className="text-blue-700 hover:text-blue-900" to={`/cg/shipment/${shipment.shipment_id}`}>
          {shortId(shipment.shipment_id)}
        </Link>
      </td>
      <td className="px-4 py-3">{shipment.triggered_by ?? "Unknown sender"}</td>
      <td className="max-w-sm px-4 py-3">
        <div className="truncate">{shipment.subject ?? customerName}</div>
        <div className="text-xs text-neutral-500">{shipment.customer_id}</div>
      </td>
      <td className="px-4 py-3">{shipment.document_runs.length}</td>
      <td className="px-4 py-3">{formatTime(shipment.triggered_at)}</td>
      <td className="px-4 py-3">
        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${state.className}`}>
          {state.label}
        </span>
        <div className="mt-1 text-xs text-neutral-500">{state.detail}</div>
      </td>
      <td className="px-4 py-3">
        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${statusClass(shipment.status)}`}>
          {shipment.status}
        </span>
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-nowrap items-center gap-2 whitespace-nowrap">
        <Link
          className={
            shipment.reply_message_id
              ? "rounded-md border border-blue-200 px-3 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-50"
              : "rounded-md bg-blue-700 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-800"
          }
          to={`/cg/shipment/${shipment.shipment_id}`}
        >
          {shipment.reply_message_id ? "View" : "Review draft"}
        </Link>
        <button
          className="rounded-md border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-60"
          disabled={isDeleting}
          onClick={handleDelete}
          type="button"
        >
          {isDeleting ? "Deleting..." : "Delete"}
        </button>
        </div>
      </td>
    </tr>
  );
}
