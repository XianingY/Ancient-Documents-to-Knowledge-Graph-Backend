import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentViewer, SegmentProofreader } from "./OcrProofreader";
import type { OcrResult } from "./types";

const ocr: OcrResult = {
  id: 1,
  image_id: 2,
  raw_text: "孔□",
  original_raw_text: "孔□",
  corrected_text: null,
  status: "done",
  confidence: 0.8,
  coverage: 0.7,
  engine: "paddle_v6_layout_multiview",
  model_versions: "test",
  segments: [
    {
      segment_id: "s0001",
      text: "孔□",
      status: "uncertain",
      confidence: 0.4,
      image_bbox: [100, 50, 200, 160],
      source_views: ["original"],
      rejection_reasons: ["uncertain:single_view_long_or_low_confidence"],
    },
  ],
  corrected_segments: [],
  rejection_reasons: [],
  crop_bbox: [0, 0, 800, 600],
  image_size: [800, 600],
  human_corrected: false,
  created_at: "2026-01-01T00:00:00",
};

describe("OCR proofreading components", () => {
  it("selects a segment from the image overlay", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <DocumentViewer
        imageUrl="blob:test"
        imageTitle="test"
        ocr={ocr}
        selectedSegmentId={null}
        onSelectSegment={onSelect}
        showOcrOverlay
      />,
    );

    const box = container.querySelector(".ocr-box");
    expect(box).not.toBeNull();
    fireEvent.click(box as Element);

    expect(onSelect).toHaveBeenCalledWith("s0001");
  });

  it("hides OCR boxes by default", () => {
    const { container } = render(
      <DocumentViewer imageUrl="blob:test" imageTitle="test" ocr={ocr} />,
    );

    expect(container.querySelector(".ocr-box")).toBeNull();
  });

  it("edits a segment and selects it on focus", () => {
    const onChange = vi.fn();
    const onSelect = vi.fn();
    render(
      <SegmentProofreader
        ocr={ocr}
        segmentTexts={{ s0001: "孔□" }}
        selectedSegmentId={null}
        onSelectSegment={onSelect}
        onChangeSegment={onChange}
      />,
    );

    const editor = screen.getByDisplayValue("孔□");
    fireEvent.focus(editor);
    fireEvent.change(editor, { target: { value: "孔珍" } });

    expect(onSelect).toHaveBeenCalledWith("s0001");
    expect(onChange).toHaveBeenCalledWith("s0001", "孔珍");
  });
});
