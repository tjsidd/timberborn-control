import type { Configuration, Snapshot } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    },
    ...init
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : `${response.status} ${response.statusText}: ${JSON.stringify(detail ?? body)}`);
  }

  return response.json() as Promise<T>;
}

export function fetchStatus(): Promise<Snapshot> {
  return request<Snapshot>("/api/status");
}

export function refreshStatus(): Promise<Snapshot> {
  return request<Snapshot>("/api/refresh", { method: "POST" });
}

export function createSession(name: string): Promise<Snapshot> {
  return request<Snapshot>("/api/sessions", { method: "POST", body: JSON.stringify({ name }) });
}

export function loadSession(id: string): Promise<Snapshot> {
  return request<Snapshot>(`/api/sessions/${encodeURIComponent(id)}/load`, { method: "POST" });
}

export function setLever(name: string, state: boolean): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/levers/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify({ state })
  });
}

export function saveConfiguration(configuration: Configuration): Promise<Snapshot> {
  return request<Snapshot>("/api/config", { method: "PUT", body: JSON.stringify(configuration) });
}

export function deleteSensor(name: string): Promise<Snapshot> {
  return request<Snapshot>(`/api/sensors/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export function setAutoDeleteMissingSensors(enabled: boolean): Promise<Snapshot> {
  return request<Snapshot>("/api/settings/sensors", {
    method: "PUT",
    body: JSON.stringify({ auto_delete_missing_sensors: enabled })
  });
}
