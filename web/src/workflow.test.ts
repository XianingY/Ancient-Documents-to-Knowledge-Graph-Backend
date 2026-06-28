import { describe, expect, it, vi } from "vitest";
import { saveOcrAndReanalyze } from "./workflow";

describe("saveOcrAndReanalyze", () => {
  it("saves OCR text and polls for new structured result and graph", async () => {
    const progress: string[] = [];
    const api = {
      updateOcrResult: vi.fn().mockResolvedValue({ id: 1 }),
      listStructuredResults: vi
        .fn()
        .mockResolvedValueOnce({ ids: [10] })
        .mockResolvedValueOnce({ ids: [10] })
        .mockResolvedValueOnce({ ids: [10, 11] }),
      createStructuredResult: vi.fn().mockResolvedValue({ success: true }),
      getStructuredResult: vi
        .fn()
        .mockResolvedValueOnce({
          id: 10,
          ocr_result_id: 1,
          content: { Seller: "旧结果" },
          status: "done",
          created_at: "2026-01-01T00:00:00",
        })
        .mockResolvedValueOnce({
          id: 10,
          ocr_result_id: 1,
          content: { Seller: "旧结果" },
          status: "done",
          created_at: "2026-01-01T00:00:00",
        })
        .mockResolvedValueOnce({
          id: 11,
          ocr_result_id: 1,
          content: { Seller: "熊某" },
          status: "done",
          created_at: "2026-01-01T00:00:01",
        }),
      listRelationGraphs: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [21] }),
      createRelationGraph: vi.fn().mockResolvedValue({ success: true }),
      getRelationGraph: vi.fn().mockResolvedValue({
        id: 21,
        structured_result_id: 11,
        content: { series: [] },
        status: "done",
        created_at: "2026-01-01T00:00:00",
      }),
    };

    const result = await saveOcrAndReanalyze(
      api,
      1,
      "修订文本",
      (step) => progress.push(step.stage),
      { sleep: async () => undefined, maxAttempts: 5 },
    );

    expect(api.updateOcrResult).toHaveBeenCalledWith(1, "修订文本");
    expect(api.createStructuredResult).toHaveBeenCalledWith(1);
    expect(api.createRelationGraph).toHaveBeenCalledWith(11);
    expect(result.structured.id).toBe(11);
    expect(result.graph.id).toBe(21);
    expect(progress).toEqual(["saving", "structured", "graph", "done"]);
  });

  it("surfaces failed structured analysis", async () => {
    const api = {
      updateOcrResult: vi.fn().mockResolvedValue({ id: 1 }),
      listStructuredResults: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [11] }),
      createStructuredResult: vi.fn().mockResolvedValue({ success: true }),
      getStructuredResult: vi.fn().mockResolvedValue({
        id: 11,
        ocr_result_id: 1,
        content: {},
        status: "failed",
        created_at: "2026-01-01T00:00:00",
      }),
      listRelationGraphs: vi.fn(),
      createRelationGraph: vi.fn(),
      getRelationGraph: vi.fn(),
    };

    await expect(
      saveOcrAndReanalyze(api, 1, "修订文本", vi.fn(), {
        sleep: async () => undefined,
        maxAttempts: 2,
      }),
    ).rejects.toThrow("结构化分析失败");
    expect(api.listRelationGraphs).not.toHaveBeenCalled();
  });

  it("accepts refreshed records when backend reuses ids", async () => {
    const api = {
      updateOcrResult: vi.fn().mockResolvedValue({ id: 1 }),
      listStructuredResults: vi
        .fn()
        .mockResolvedValueOnce({ ids: [11] })
        .mockResolvedValueOnce({ ids: [11] })
        .mockResolvedValueOnce({ ids: [11] }),
      createStructuredResult: vi.fn().mockResolvedValue({ success: true }),
      getStructuredResult: vi
        .fn()
        .mockResolvedValueOnce({
          id: 11,
          ocr_result_id: 1,
          content: { Seller: "旧结果" },
          status: "done",
          created_at: "2026-01-01T00:00:00",
        })
        .mockResolvedValueOnce({
          id: 11,
          ocr_result_id: 1,
          content: {},
          status: "processing",
          created_at: "2026-01-01T00:00:02",
        })
        .mockResolvedValueOnce({
          id: 11,
          ocr_result_id: 1,
          content: { Seller: "新结果" },
          status: "done",
          created_at: "2026-01-01T00:00:03",
        }),
      listRelationGraphs: vi
        .fn()
        .mockResolvedValueOnce({ ids: [31] })
        .mockResolvedValueOnce({ ids: [31] }),
      createRelationGraph: vi.fn().mockResolvedValue({ success: true }),
      getRelationGraph: vi
        .fn()
        .mockResolvedValueOnce({
          id: 31,
          structured_result_id: 11,
          content: {},
          status: "done",
          created_at: "2026-01-01T00:00:00",
        })
        .mockResolvedValueOnce({
          id: 31,
          structured_result_id: 11,
          content: { series: [] },
          status: "done",
          created_at: "2026-01-01T00:00:04",
        }),
    };

    const result = await saveOcrAndReanalyze(api, 1, "修订文本", vi.fn(), {
      sleep: async () => undefined,
      maxAttempts: 5,
    });

    expect(result.structured.id).toBe(11);
    expect(result.graph.id).toBe(31);
  });

  it("saves corrected text without segment edits", async () => {
    const api = {
      updateOcrResult: vi.fn().mockResolvedValue({ id: 1 }),
      createStructuredResult: vi.fn().mockResolvedValue({ success: true }),
      listStructuredResults: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [11] }),
      getStructuredResult: vi.fn().mockResolvedValue({
        id: 11,
        ocr_result_id: 1,
        content: {},
        status: "done",
        created_at: "2026-01-01T00:00:00",
      }),
      listRelationGraphs: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [21] }),
      createRelationGraph: vi.fn().mockResolvedValue({ success: true }),
      getRelationGraph: vi.fn().mockResolvedValue({
        id: 21,
        structured_result_id: 11,
        content: {},
        status: "done",
        created_at: "2026-01-01T00:00:00",
      }),
    };

    await saveOcrAndReanalyze(api, 1, "孔珍", vi.fn(), {
      sleep: async () => undefined,
      maxAttempts: 3,
    });

    expect(api.updateOcrResult).toHaveBeenCalledWith(1, "孔珍");
  });

  it("uses stable structured and graph endpoints", async () => {
    const progress: string[] = [];
    const api = {
      updateOcrResult: vi.fn().mockResolvedValue({ id: 1 }),
      createStructuredResult: vi.fn().mockResolvedValue({ success: true }),
      listStructuredResults: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [11] }),
      getStructuredResult: vi.fn().mockResolvedValue({
        id: 11,
        ocr_result_id: 1,
        content: { Seller: "邵長春" },
        status: "done",
        created_at: "2026-01-01T00:00:01",
      }),
      listRelationGraphs: vi
        .fn()
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [] })
        .mockResolvedValueOnce({ ids: [21] }),
      createRelationGraph: vi.fn().mockResolvedValue({ success: true }),
      getRelationGraph: vi.fn().mockResolvedValue({
        id: 21,
        structured_result_id: 11,
        content: { series: [] },
        status: "done",
        created_at: "2026-01-01T00:00:02",
      }),
    };

    const result = await saveOcrAndReanalyze(
      api,
      1,
      "修订文本",
      (step) => progress.push(step.stage),
      { sleep: async () => undefined, maxAttempts: 5 },
    );

    expect(api.createStructuredResult).toHaveBeenCalledWith(1);
    expect(api.createRelationGraph).toHaveBeenCalledWith(11);
    expect(result.structured.id).toBe(11);
    expect(result.graph.id).toBe(21);
    expect(progress).toEqual(["saving", "structured", "graph", "done"]);
  });
});
