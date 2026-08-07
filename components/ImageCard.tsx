"use client";

import type { Decision, ReviewImage, Tag } from "@/lib/types";
import { TagEditor } from "./TagEditor";

export function ImageCard({ image, tags, selectedTags, decision, saving, onTagsChange, onCreateTag, onDecision }: {
  image: ReviewImage;
  tags: Tag[];
  selectedTags: Tag[];
  decision?: Decision;
  saving?: boolean;
  onTagsChange: (tags: Tag[]) => void;
  onCreateTag: (label: string) => Promise<Tag | null>;
  onDecision: (decision: Decision) => void;
}) {
  return (
    <article className={`image-card ${decision ?? ""}`}>
      <a href={image.image_url} target="_blank" rel="noreferrer"><img src={image.thumbnail_url} alt={image.image_id} loading="lazy" /></a>
      <div className="image-card-body">
        <div className="image-title-row"><h3>{image.image_id}</h3>{decision && <span className={`decision-pill ${decision}`}>{decision}</span>}</div>
        <p className="image-meta">{image.captured_at ? new Date(image.captured_at).toLocaleDateString() : "Date unavailable"}{image.latitude != null ? ` · ${image.latitude.toFixed(2)}, ${image.longitude?.toFixed(2)}` : ""}</p>
        <p className="team-votes">Team · {image.team_reviews.accept} accept · {image.team_reviews.uncertain} uncertain · {image.team_reviews.reject} reject</p>
        <TagEditor selected={selectedTags} available={tags} onChange={onTagsChange} onCreate={onCreateTag} />
        <div className="decision-buttons">
          <button disabled={saving} className="reject" onClick={() => onDecision("reject")}>Reject</button>
          <button disabled={saving} className="uncertain" onClick={() => onDecision("uncertain")}>Uncertain</button>
          <button disabled={saving} className="accept" onClick={() => onDecision("accept")}>Accept</button>
        </div>
      </div>
    </article>
  );
}
