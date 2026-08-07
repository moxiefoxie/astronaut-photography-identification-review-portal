"use client";

import { useMemo, useState } from "react";
import type { Tag } from "@/lib/types";

export function TagEditor({
  selected,
  available,
  onChange,
  onCreate,
}: {
  selected: Tag[];
  available: Tag[];
  onChange: (tags: Tag[]) => void;
  onCreate: (label: string) => Promise<Tag | null>;
}) {
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const selectedIds = useMemo(() => new Set(selected.map((tag) => tag.id)), [selected]);
  const matches = available.filter((tag) => !selectedIds.has(tag.id) && tag.label.toLowerCase().includes(query.toLowerCase())).slice(0, 8);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const exact = available.find((tag) => tag.label.toLowerCase() === query.trim().toLowerCase());
    const tag = exact ?? (await onCreate(query.trim()));
    if (tag && !selectedIds.has(tag.id)) onChange([...selected, tag]);
    setQuery("");
    setAdding(false);
  }

  return (
    <div className="tag-editor">
      <div className="tag-list">
        {selected.map((tag) => (
          <button
            className="tag-chip"
            style={{ "--tag-color": tag.color } as React.CSSProperties}
            key={tag.id}
            title={`Remove ${tag.label}`}
            onClick={() => onChange(selected.filter((value) => value.id !== tag.id))}
            type="button"
          >
            {tag.label}<b>×</b>
          </button>
        ))}
        <button className="add-tag-button" type="button" onClick={() => setAdding((value) => !value)}>+ Add tag</button>
      </div>
      {adding && (
        <form className="tag-combobox" onSubmit={submit}>
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search or create a tag" />
          <div className="tag-options">
            {matches.map((tag) => <button type="button" key={tag.id} onClick={() => { onChange([...selected, tag]); setAdding(false); setQuery(""); }}>{tag.label}</button>)}
            {query.trim() && !available.some((tag) => tag.label.toLowerCase() === query.trim().toLowerCase()) && <button type="submit">Create “{query.trim()}”</button>}
          </div>
        </form>
      )}
    </div>
  );
}
