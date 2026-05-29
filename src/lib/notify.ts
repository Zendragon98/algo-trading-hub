import { toast } from "sonner";

export function notifySuccess(message: string) {
  toast.success(message);
}

export function notifyError(err: unknown, fallback = "Request failed") {
  const message = err instanceof Error ? err.message : fallback;
  toast.error(message);
}
