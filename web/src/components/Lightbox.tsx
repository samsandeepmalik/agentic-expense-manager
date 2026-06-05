export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(45,42,36,.7)",
                  display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60 }}
         onClick={onClose}>
      <img src={src} style={{ maxWidth: "85vw", maxHeight: "85vh", borderRadius: 12 }} />
    </div>
  );
}
