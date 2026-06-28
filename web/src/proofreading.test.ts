import { describe, expect, it } from "vitest";
import type { OcrResult } from "./types";
import {
  bboxToPercent,
  buildSegmentEdits,
  composeTextFromSegments,
  initialSegmentTexts,
} from "./proofreading";

const baseOcr: OcrResult = {
  id: 1,
  image_id: 2,
  raw_text: "立永賣\n孔□",
  original_raw_text: "立永賣\n孔□",
  status: "done",
  confidence: 0.8,
  coverage: 0.7,
  segments: [
    { segment_id: "s0000", text: "立永賣", image_bbox: [100, 50, 300, 150] },
    { segment_id: "s0001", text: "孔□", image_bbox: [320, 50, 420, 150] },
  ],
  corrected_segments: [{ segment_id: "s0001", text: "孔珍" }],
  rejection_reasons: [],
  crop_bbox: [0, 0, 800, 600],
  image_size: [800, 600],
  human_corrected: true,
  created_at: "2026-01-01T00:00:00",
};

describe("proofreading helpers", () => {
  it("initializes segment text from existing corrections", () => {
    expect(initialSegmentTexts(baseOcr)).toEqual({
      s0000: "立永賣",
      s0001: "孔珍",
    });
  });

  it("composes full text and returns only changed segment edits", () => {
    const texts = { s0000: "立永賣", s0001: "孔珍", s0002: "忽略" };

    expect(composeTextFromSegments(baseOcr, texts)).toBe("立永賣\n孔珍");
    expect(buildSegmentEdits(baseOcr, { ...texts, s0000: "立永賣田契" })).toEqual([
      { segment_id: "s0000", text: "立永賣田契" },
    ]);
  });

  it("maps original image bbox to overlay percentages", () => {
    expect(bboxToPercent([100, 50, 300, 150], [800, 500])).toEqual({
      left: 12.5,
      top: 10,
      width: 25,
      height: 20,
    });
  });
});
