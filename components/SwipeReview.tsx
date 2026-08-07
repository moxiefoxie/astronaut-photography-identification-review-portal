"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Decision, ReviewImage, Tag } from "@/lib/types";
import { TagEditor } from "./TagEditor";

export function SwipeReview({ image, position, total, tags, selectedTags, saving, waitingForMore, onTagsChange, onCreateTag, onDecision }: {
  image: ReviewImage | null;
  position: number;
  total: number;
  tags: Tag[];
  selectedTags: Tag[];
  saving: boolean;
  waitingForMore: boolean;
  onTagsChange: (tags: Tag[]) => void;
  onCreateTag: (label: string) => Promise<Tag | null>;
  onDecision: (decision: Decision) => void;
}) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const [dragX, setDragX] = useState(0);
  const decide = useCallback((decision: Decision) => { if (!saving) { setDragX(0); onDecision(decision); } }, [onDecision, saving]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.target as HTMLElement)?.tagName === "INPUT") return;
      if (event.key === "ArrowLeft") decide("reject");
      if (event.key === "ArrowRight") decide("accept");
      if (event.key === "ArrowUp") decide("uncertain");
      if (event.key === "ArrowDown") decide("skip");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide]);

  if (!image) return <section className="queue-empty">{waitingForMore ? <div className="spinner" /> : <div className="completion-mark">✓</div>}<h2>{waitingForMore ? "Waiting for the next candidates…" : "You’re caught up."}</h2><p>{waitingForMore ? "The queue will update automatically as the pipeline publishes results." : "Every currently available candidate has a decision from you."}</p></section>;

  return (
    <section className="swipe-shell">
      <div className="swipe-progress"><span>{position + 1} of {total}</span><span>← reject · ↑ uncertain · accept →</span></div>
      <article
        className="swipe-card"
        style={{ transform: `translateX(${dragX}px) rotate(${dragX / 30}deg)`, opacity: Math.max(0.55, 1 - Math.abs(dragX) / 500) }}
        onPointerDown={(event) => {
          const target = event.target as HTMLElement;
          if (target.closest("button, input, textarea, select, option, form, a, label, [role='button']")) return;
          start.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => { if (start.current) setDragX(event.clientX - start.current.x); }}
        onPointerUp={(event) => {
          if (!start.current) return;
          const dx = event.clientX - start.current.x;
          start.current = null;
          if (Math.abs(dx) > 80) decide(dx > 0 ? "accept" : "reject");
          else setDragX(0);
        }}
        onPointerCancel={() => { start.current = null; setDragX(0); }}
      >
        {dragX < -35 && <div className="swipe-stamp reject">REJECT</div>}
        {dragX > 35 && <div className="swipe-stamp accept">ACCEPT</div>}
        <a href={image.image_url} target="_blank" rel="noreferrer"><img src={image.thumbnail_url} alt={image.image_id} draggable={false} /></a>
        <div className="swipe-card-body">
          <h2>{image.image_id}</h2>
          <p className="image-meta">{image.captured_at ? new Date(image.captured_at).toLocaleString() : "Capture time unavailable"}{image.latitude != null ? ` · ${image.latitude.toFixed(2)}, ${image.longitude?.toFixed(2)}` : ""}</p>
          <p className="team-votes">Team votes · {image.team_reviews.accept} accept · {image.team_reviews.uncertain} uncertain · {image.team_reviews.reject} reject</p>
          <TagEditor selected={selectedTags} available={tags} onChange={onTagsChange} onCreate={onCreateTag} />
        </div>
      </article>
      <div className="swipe-actions">
        <button className="round reject" onClick={() => decide("reject")} aria-label="Reject">×</button>
        <button className="round skip" onClick={() => decide("skip")} aria-label="Skip">↷</button>
        <button className="round uncertain" onClick={() => decide("uncertain")} aria-label="Uncertain">?</button>
        <button className="round accept" onClick={() => decide("accept")} aria-label="Accept">✓</button>
      </div>
    </section>
  );
}
