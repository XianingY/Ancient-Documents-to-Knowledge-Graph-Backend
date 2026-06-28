import type { OcrResult, OcrSegment } from "./types";

export type SegmentEditPayload = {
  segment_id: string;
  text: string;
};

export function segmentKey(segment: OcrSegment, index: number) {
  return String(segment.segment_id ?? index);
}

export function correctedSegmentMap(ocr: OcrResult | null) {
  const map: Record<string, string> = {};
  for (const item of ocr?.corrected_segments ?? []) {
    map[String(item.segment_id)] = item.text;
  }
  return map;
}

export function initialSegmentTexts(ocr: OcrResult | null) {
  const corrected = correctedSegmentMap(ocr);
  const texts: Record<string, string> = {};
  for (const [index, segment] of (ocr?.segments ?? []).entries()) {
    const key = segmentKey(segment, index);
    texts[key] = corrected[key] ?? segment.text ?? "";
  }
  return texts;
}

export function composeTextFromSegments(
  ocr: OcrResult | null,
  segmentTexts: Record<string, string>,
) {
  const segments = ocr?.segments ?? [];
  if (!segments.length) {
    return ocr?.raw_text ?? "";
  }
  return segments
    .map((segment, index) => segmentTexts[segmentKey(segment, index)] ?? segment.text ?? "")
    .filter((text) => text.length > 0)
    .join("\n");
}

export function buildSegmentEdits(
  ocr: OcrResult | null,
  segmentTexts: Record<string, string>,
): SegmentEditPayload[] {
  if (!ocr) {
    return [];
  }
  const corrected = correctedSegmentMap(ocr);
  const edits: SegmentEditPayload[] = [];
  for (const [index, segment] of ocr.segments.entries()) {
    const key = segmentKey(segment, index);
    const current = segmentTexts[key] ?? "";
    const baseline = corrected[key] ?? segment.text ?? "";
    if (current !== baseline) {
      edits.push({ segment_id: key, text: current });
    }
  }
  return edits;
}

export function bboxToPercent(
  bbox: number[] | undefined,
  imageSize: number[] | undefined,
) {
  if (!bbox || bbox.length < 4 || !imageSize || imageSize.length < 2) {
    return null;
  }
  const [width, height] = imageSize;
  if (!width || !height) {
    return null;
  }
  return {
    left: (bbox[0] / width) * 100,
    top: (bbox[1] / height) * 100,
    width: ((bbox[2] - bbox[0]) / width) * 100,
    height: ((bbox[3] - bbox[1]) / height) * 100,
  };
}

export function segmentTone(segment: OcrSegment) {
  const text = segment.text ?? "";
  if (segment.status === "rejected") {
    return "rejected";
  }
  if (text.includes("□") || segment.status === "uncertain") {
    return "uncertain";
  }
  if (segment.status === "partial") {
    return "partial";
  }
  return "accepted";
}
