import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiRequest, clearAccessToken, saveAccessToken } from "./api";

describe("apiRequest", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("sends the stored bearer token and serializes request data", async () => {
    saveAccessToken("session-token");
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await expect(apiRequest("/auth/me", { method: "POST", body: { email: "hr@example.com" } })).resolves.toEqual({ ok: true });
    expect(fetch.mock.calls[0][1].headers.get("Authorization")).toBe("Bearer session-token");
    expect(fetch.mock.calls[0][1].body).toBe(JSON.stringify({ email: "hr@example.com" }));
  });

  it("surfaces API validation errors and clears session only when explicitly requested", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Invite code expired" }), { status: 400 }));
    vi.stubGlobal("fetch", fetch);
    await expect(apiRequest("/auth/signup", { method: "POST", body: {} })).rejects.toThrow("Invite code expired");
    saveAccessToken("token");
    clearAccessToken();
    expect(sessionStorage.getItem("hrms_access_token")).toBeNull();
  });
});

