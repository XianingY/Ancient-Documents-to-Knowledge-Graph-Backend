import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("logs in and stores the bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        success: true,
        access_token: "token-123",
        token_type: "bearer",
        user_id: 7,
        username: "demo_web",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = new ApiClient("/api");
    const result = await api.login("demo_web", "DemoWeb2026!");

    expect(result.access_token).toBe("token-123");
    expect(api.getToken()).toBe("token-123");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends authorization and JSON when saving OCR text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        success: true,
        data: {
          id: 9,
          image_id: 3,
          raw_text: "原始文本",
          original_raw_text: "原始文本",
          corrected_text: "修订文本",
          status: "done",
          confidence: 0.42,
          coverage: 0.58,
          segments: [],
          corrected_segments: [],
          rejection_reasons: [],
          crop_bbox: [],
          image_size: [],
          human_corrected: true,
          created_at: "2026-01-01T00:00:00",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = new ApiClient("/api", "token-abc");
    await api.updateOcrResult(9, "修订文本");

    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-abc");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ corrected_text: "修订文本" }));
  });

  it("sends corrected text without segment edits when saving OCR text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        success: true,
        data: {
          id: 9,
          image_id: 3,
          raw_text: "孔□",
          original_raw_text: "孔□",
          corrected_text: "孔珍",
          status: "done",
          confidence: 0.4,
          coverage: 0.7,
          segments: [],
          corrected_segments: [{ segment_id: "s0001", text: "孔珍" }],
          rejection_reasons: [],
          crop_bbox: [],
          image_size: [],
          human_corrected: true,
          created_at: "2026-01-01T00:00:00",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const api = new ApiClient("/api", "token-abc");
    await api.updateOcrResult(9, "孔珍");

    expect((fetchMock.mock.calls[0][1].body as string)).toBe(
      JSON.stringify({
        corrected_text: "孔珍",
      }),
    );
  });

  it("raises ApiError with backend detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "未授权" }, { status: 401 })));

    const api = new ApiClient("/api");
    await expect(api.currentUser()).rejects.toMatchObject({
      status: 401,
      message: "未授权",
    });
  });
});
