import { PointerEvent, useState } from "react";
import { RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import type { OcrResult } from "./types";
import { bboxToPercent, segmentKey, segmentTone } from "./proofreading";

type DocumentViewerProps = {
  imageUrl: string;
  imageTitle: string;
  ocr?: OcrResult | null;
  selectedSegmentId?: string | null;
  onSelectSegment?: (segmentId: string) => void;
  showOcrOverlay?: boolean;
};

type SegmentProofreaderProps = {
  ocr: OcrResult | null;
  segmentTexts: Record<string, string>;
  selectedSegmentId: string | null;
  onSelectSegment: (segmentId: string) => void;
  onChangeSegment: (segmentId: string, text: string) => void;
};

function percent(value: number) {
  return `${Number.isFinite(value) ? value : 0}%`;
}

function confidenceLabel(value?: number) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

export function DocumentViewer({
  imageUrl,
  imageTitle,
  ocr = null,
  selectedSegmentId = null,
  onSelectSegment,
  showOcrOverlay = false,
}: DocumentViewerProps) {
  const [naturalSize, setNaturalSize] = useState<number[]>([]);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragStart, setDragStart] = useState<{ x: number; y: number; panX: number; panY: number } | null>(
    null,
  );
  const imageSize = ocr?.image_size?.length === 2 ? ocr.image_size : naturalSize;

  function resetView() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragStart({ x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y });
  }

  function drag(event: PointerEvent<HTMLDivElement>) {
    if (!dragStart) {
      return;
    }
    setPan({
      x: dragStart.panX + event.clientX - dragStart.x,
      y: dragStart.panY + event.clientY - dragStart.y,
    });
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    if (dragStart) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragStart(null);
  }

  return (
    <div className="image-viewer">
      <div className="viewer-toolbar">
        <button type="button" className="icon-button" onClick={() => setZoom((value) => Math.min(value + 0.2, 3))} title="放大">
          <ZoomIn size={16} />
        </button>
        <button type="button" className="icon-button" onClick={() => setZoom((value) => Math.max(value - 0.2, 0.6))} title="缩小">
          <ZoomOut size={16} />
        </button>
        <button type="button" className="icon-button" onClick={resetView} title="重置视图">
          <RotateCcw size={16} />
        </button>
      </div>
      <div
        className={`image-viewport ${dragStart ? "dragging" : ""}`}
        onPointerDown={startDrag}
        onPointerMove={drag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <div
          className="image-canvas"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
          <img
            src={imageUrl}
            alt={imageTitle}
            onLoad={(event) =>
              setNaturalSize([
                event.currentTarget.naturalWidth,
                event.currentTarget.naturalHeight,
              ])
            }
          />
          {showOcrOverlay ? (
            <div className="ocr-overlay" aria-label="OCR segments">
              {(ocr?.segments ?? []).map((segment, index) => {
              const key = segmentKey(segment, index);
              const box = bboxToPercent(segment.image_bbox ?? segment.bbox, imageSize);
              if (!box) {
                return null;
              }
              return (
                <button
                  type="button"
                  key={key}
                  className={`ocr-box ${segmentTone(segment)} ${selectedSegmentId === key ? "selected" : ""}`}
                  style={{
                    left: percent(box.left),
                    top: percent(box.top),
                    width: percent(box.width),
                    height: percent(box.height),
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => onSelectSegment?.(key)}
                  title={segment.text ?? ""}
                />
              );
              })}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function SegmentProofreader({
  ocr,
  segmentTexts,
  selectedSegmentId,
  onSelectSegment,
  onChangeSegment,
}: SegmentProofreaderProps) {
  const segments = ocr?.segments ?? [];
  if (!segments.length) {
    return <div className="segment-empty">暂无 OCR 框</div>;
  }

  return (
    <div className="segment-list">
      {segments.map((segment, index) => {
        const key = segmentKey(segment, index);
        const selected = selectedSegmentId === key;
        return (
          <div key={key} className={`segment-card ${segmentTone(segment)} ${selected ? "selected" : ""}`}>
            <button type="button" className="segment-meta" onClick={() => onSelectSegment(key)}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{segment.status ?? "unknown"}</strong>
              <small>{confidenceLabel(segment.confidence)}</small>
              <small>{(segment.source_views ?? []).join(" / ")}</small>
            </button>
            <textarea
              value={segmentTexts[key] ?? segment.text ?? ""}
              onFocus={() => onSelectSegment(key)}
              onChange={(event) => onChangeSegment(key, event.target.value)}
            />
            {segment.rejection_reasons?.length ? (
              <div className="segment-reasons">{segment.rejection_reasons.join(" · ")}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
