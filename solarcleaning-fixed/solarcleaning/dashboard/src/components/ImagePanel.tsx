import { imageSrc } from "../api";
import { clock } from "../format";
import type { ImageInfo } from "../types";

export default function ImagePanel({ image }: { image: ImageInfo | null }) {
  if (!image) return <p className="note">No image received yet. Start tools/capture.py.</p>;
  const conf = image.confidence === null ? "n/a" : `${Math.round(image.confidence * 100)}%`;
  return (
    <figure className="photo">
      <img src={imageSrc(image.url, image.id)} alt={`Latest panel photo, classified ${image.label ?? "unknown"}`} />
      <figcaption>
        <span className={`pill ${image.model === "cnn" ? "pill-neutral" : "pill-warn"}`}>
          {image.model === "cnn" ? "CNN" : "Stub"}
        </span>
        <span>
          {image.label ?? "unlabelled"}, confidence {conf}
        </span>
        <span className="sub">
          {image.context.replace("_", " ")} · {clock(image.timestamp)}
        </span>
        {!image.used && <span className="note">Not used for decisions: {image.note ?? "flagged"}.</span>}
      </figcaption>
    </figure>
  );
}
