const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export class ApiError extends Error {
  constructor(message, status, code, details) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function formatErrorMessage(errorData, status) {
  if (typeof errorData?.message === "string") {
    return errorData.message;
  }

  if (typeof errorData?.detail === "string") {
    return errorData.detail;
  }

  if (Array.isArray(errorData?.detail)) {
    const messages = errorData.detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return null;

        const field = Array.isArray(item.loc) ? item.loc.at(-1) : null;
        const message = typeof item.msg === "string"
          ? item.msg.replace(/^Value error,\s*/i, "")
          : null;

        if (!message) return null;
        return field && field !== "body" ? `${field}: ${message}` : message;
      })
      .filter(Boolean);

    if (messages.length > 0) {
      return messages.join(" · ");
    }
  }

  return typeof errorData === "string"
    ? errorData
    : `Request failed with status ${status}`;
}

/**
 * Core API request handler
 */
export async function apiClient(endpoint, options = {}) {
  const url = endpoint.startsWith("http") ? endpoint : `${BASE_URL}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

  const headers = {
    Accept: "application/json",
    ...options.headers,
  };

  // If body is not FormData, default to JSON
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers,
    });
  } catch (error) {
    const message = error?.name === "AbortError"
      ? "The request took too long. Please try again."
      : "Avoria could not connect to the API. Make sure the backend service is running, then try again.";
    const code = error?.name === "AbortError" ? "request_timeout" : "network_error";
    throw new ApiError(message, 0, code, null);
  }

  if (!response.ok) {
    let errorData = null;
    try {
      errorData = await response.json();
    } catch {
      // response is not JSON
    }

    const message = formatErrorMessage(errorData, response.status);

    const code = errorData?.code || "api_error";
    const details = errorData?.details || null;

    throw new ApiError(message, response.status, code, details);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return null;
  }

  return response.json();
}
