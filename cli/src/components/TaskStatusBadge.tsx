import { Text } from "ink";
import type { TaskStatus } from "../types.js";

const STATUS_COLORS: Record<TaskStatus, string> = {
  PENDING: "gray",
  RUNNING: "cyan",
  SUCCEEDED: "green",
  FAILED: "red",
  RETRYING: "yellow",
  CANCELLED: "dim",
  COMPENSATING: "yellow",
  COMPENSATED: "green",
  COMPENSATION_FAILED: "red",
};

const STATUS_LABELS: Record<TaskStatus, string> = {
  PENDING: "pend",
  RUNNING: "run",
  SUCCEEDED: "done",
  FAILED: "fail",
  RETRYING: "retry",
  CANCELLED: "skip",
  COMPENSATING: "undo",
  COMPENSATED: "undone",
  COMPENSATION_FAILED: "underr",
};

interface Props {
  status: TaskStatus;
}

export function TaskStatusBadge({ status }: Props) {
  const color = STATUS_COLORS[status] ?? "white";
  const label = STATUS_LABELS[status] ?? status;
  return (
    <Text color={color}>
      [{label}]
    </Text>
  );
}
