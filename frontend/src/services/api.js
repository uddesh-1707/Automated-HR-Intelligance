const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function apiRequest(path, options = {}) {
  const token = sessionStorage.getItem("hrms_access_token");
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body !== undefined && !isFormData) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : isFormData ? options.body : JSON.stringify(options.body)
    });
  } catch {
    throw new ApiError("Could not reach the HRMS API. Check that the backend is running.", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = typeof body.detail === "string" ? body.detail : "The request could not be completed.";
    throw new ApiError(message, response.status);
  }
  if (options.responseType === "blob") return response.blob();
  if (response.status === 204) return null;
  return response.json();
}

export function saveAccessToken(token) {
  sessionStorage.setItem("hrms_access_token", token);
}

export function clearAccessToken() {
  sessionStorage.removeItem("hrms_access_token");
}
